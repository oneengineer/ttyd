#include <fcntl.h>
#include <json.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "server.h"
#include "image-upload.h"
#include "utils.h"
#include "compat.h"

#ifndef O_BINARY
#define O_BINARY 0
#endif

// Use libuv filesystem calls throughout: paths are UTF-8 on native Windows too.
static void remove_path(const char *path, bool directory) {
  uv_fs_t req;
  if (directory)
    uv_fs_rmdir(NULL, &req, path, NULL);
  else
    uv_fs_unlink(NULL, &req, path, NULL);
  uv_fs_req_cleanup(&req);
}

void image_upload_abort(struct pss_tty *pss) {
  uv_fs_t req;
  if (pss->image_fd >= 0) {
    uv_fs_close(NULL, &req, pss->image_fd, NULL);
    uv_fs_req_cleanup(&req);
    pss->image_fd = -1;
  }
  if (pss->image_path != NULL) {
    remove_path(pss->image_path, false);
    free(pss->image_path);
    pss->image_path = NULL;
  }
  if (pss->image_directory != NULL) {
    remove_path(pss->image_directory, true);
    free(pss->image_directory);
    pss->image_directory = NULL;
  }
  pss->image_received = 0;
  pss->image_size = 0;
}

static void reply(struct pss_tty *pss, const char *status, const char *value) {
  json_object *obj = json_object_new_object();
  json_object_object_add(obj, "id", json_object_new_string(pss->image_id));
  json_object_object_add(obj, "status", json_object_new_string(status));
  if (value != NULL)
    json_object_object_add(obj, strcmp(status, "complete") == 0 ? "path" : "error", json_object_new_string(value));
  free(pss->image_reply);
  pss->image_reply = strdup(json_object_to_json_string_ext(obj, JSON_C_TO_STRING_PLAIN));
  json_object_put(obj);
  lws_callback_on_writable(pss->wsi);
}

static void fail(struct pss_tty *pss, const char *message) {
  image_upload_abort(pss);
  lws_set_timer_usecs(pss->wsi, LWS_SET_TIMER_USEC_CANCEL);
  reply(pss, "error", message);
}

// Successful images survive browser disconnects and ttyd restarts. Only our own
// private, expired directories are pruned; never follow links or recurse.
static void prune_expired(const char *base) {
  static time_t last_prune;
  time_t now = time(NULL);
  if (now - last_prune < 60) return;
  last_prune = now;
  uv_fs_t scan;
  if (uv_fs_scandir(NULL, &scan, base, 0, NULL) < 0) {
    uv_fs_req_cleanup(&scan);
    return;
  }
#ifndef _WIN32
  uv_passwd_t owner;
  bool have_owner = uv_os_get_passwd(&owner) == 0;
#endif
  uv_dirent_t entry;
  while (uv_fs_scandir_next(&scan, &entry) == 0) {
    if (strlen(entry.name) != 17 || strncmp(entry.name, "ttyd-image-", 11) != 0) continue;
    char directory[4096], path[4096];
    if (snprintf(directory, sizeof(directory), "%s/%s", base, entry.name) >= sizeof(directory)) continue;
    uv_fs_t stat;
    int err = uv_fs_lstat(NULL, &stat, directory, NULL);
    bool expired = err == 0 && S_ISDIR(stat.statbuf.st_mode) && now - stat.statbuf.st_mtim.tv_sec > 86400;
#ifndef _WIN32
    expired = expired && have_owner && stat.statbuf.st_uid == owner.uid && (stat.statbuf.st_mode & 0077) == 0;
#endif
    uv_fs_req_cleanup(&stat);
    if (!expired) continue;
    const char *names[] = {"image.png", "image.jpg", "image.webp", "image.gif"};
    for (int i = 0; i < 4; i++) {
      if (snprintf(path, sizeof(path), "%s/%s", directory, names[i]) < sizeof(path)) remove_path(path, false);
    }
    remove_path(directory, true);
  }
#ifndef _WIN32
  if (have_owner) uv_os_free_passwd(&owner);
#endif
  uv_fs_req_cleanup(&scan);
}

static const char *extension(const char *mime) {
  if (strcmp(mime, "image/png") == 0) return "png";
  if (strcmp(mime, "image/jpeg") == 0) return "jpg";
  if (strcmp(mime, "image/webp") == 0) return "webp";
  if (strcmp(mime, "image/gif") == 0) return "gif";
  return NULL;
}

static bool valid_header(struct pss_tty *pss) {
  const unsigned char *p = pss->image_header;
  size_t n = pss->image_received;
  if (endswith(pss->image_path, ".png")) return n >= 8 && memcmp(p, "\x89PNG\r\n\x1a\n", 8) == 0;
  if (endswith(pss->image_path, ".jpg")) return n >= 3 && p[0] == 255 && p[1] == 216 && p[2] == 255;
  if (endswith(pss->image_path, ".gif"))
    return n >= 6 && (memcmp(p, "GIF87a", 6) == 0 || memcmp(p, "GIF89a", 6) == 0);
  return n >= 12 && memcmp(p, "RIFF", 4) == 0 && memcmp(p + 8, "WEBP", 4) == 0;
}

static void begin(struct pss_tty *pss, const char *data, size_t len) {
  image_upload_abort(pss);
  pss->image_id[0] = '\0';
  json_tokener *tok = json_tokener_new();
  json_object *obj = json_tokener_parse_ex(tok, data, (int)len);
  json_tokener_free(tok);
  json_object *id, *type, *size;
  if (obj == NULL || !json_object_is_type(obj, json_type_object) ||
      !json_object_object_get_ex(obj, "id", &id) || !json_object_is_type(id, json_type_string) ||
      !json_object_object_get_ex(obj, "type", &type) || !json_object_is_type(type, json_type_string) ||
      !json_object_object_get_ex(obj, "size", &size) || !json_object_is_type(size, json_type_int)) {
    if (obj != NULL) json_object_put(obj);
    fail(pss, "Invalid image upload request");
    return;
  }
  const char *request_id = json_object_get_string(id);
  size_t id_len = json_object_get_string_len(id);
  const char *ext = extension(json_object_get_string(type));
  int64_t bytes = json_object_get_int64(size);
  if (id_len == 0 || id_len >= sizeof(pss->image_id) || strlen(request_id) != id_len) {
    json_object_put(obj);
    fail(pss, "Invalid image upload identifier");
    return;
  }
  memcpy(pss->image_id, request_id, id_len + 1);
  json_object_put(obj);
  if (ext == NULL || bytes <= 0 || bytes > IMAGE_UPLOAD_MAX_BYTES) {
    fail(pss, "Use PNG, JPEG, WebP or GIF up to 20 MiB");
    return;
  }
  char base[4096], directory[4096], path[4096];
  size_t base_len = sizeof(base);
  if (server->image_upload_dir != NULL) {
    if (snprintf(base, sizeof(base), "%s", server->image_upload_dir) >= sizeof(base)) {
      fail(pss, "Image upload directory is too long");
      return;
    }
  } else if (uv_os_tmpdir(base, &base_len) != 0) {
    fail(pss, "Cannot find the temporary directory");
    return;
  }
  prune_expired(base);
  if (snprintf(directory, sizeof(directory), "%s/ttyd-image-XXXXXX", base) >= sizeof(directory)) {
    fail(pss, "Image upload directory is too long");
    return;
  }
  uv_fs_t req;
  int err = uv_fs_mkdtemp(NULL, &req, directory, NULL);
  if (err == 0) pss->image_directory = strdup(req.path);
  uv_fs_req_cleanup(&req);
  if (err < 0) {
    fail(pss, "Cannot create a private image directory");
    return;
  }
  if (snprintf(path, sizeof(path), "%s/image.%s", pss->image_directory, ext) >= sizeof(path)) {
    fail(pss, "Image upload path is too long");
    return;
  }
  pss->image_path = strdup(path);
  pss->image_fd = uv_fs_open(NULL, &req, path, O_WRONLY | O_CREAT | O_EXCL | O_BINARY, 0600, NULL);
  uv_fs_req_cleanup(&req);
  if (pss->image_fd < 0) {
    fail(pss, "Cannot save the image");
    return;
  }
  pss->image_size = (size_t)bytes;
  memset(pss->image_header, 0, sizeof(pss->image_header));
  lws_set_timer_usecs(pss->wsi, 30 * LWS_USEC_PER_SEC);
  reply(pss, "ready", NULL);
}

void image_upload_timeout(struct pss_tty *pss) { fail(pss, "Image upload timed out; please paste it again"); }

void image_upload_message(struct pss_tty *pss, char command, const char *data, size_t len) {
  if (!server->image_upload || !server->writable || pss->process == NULL) {
    fail(pss, "Image uploads are disabled on this terminal");
    return;
  }
  if (command == IMAGE_UPLOAD_BEGIN) {
    begin(pss, data, len);
    return;
  }
  if (command == IMAGE_UPLOAD_CANCEL) {
    image_upload_abort(pss);
    lws_set_timer_usecs(pss->wsi, LWS_SET_TIMER_USEC_CANCEL);
    reply(pss, "cancelled", NULL);
    return;
  }
  if (pss->image_fd < 0) {
    fail(pss, "No image upload is in progress");
    return;
  }
  if (command == IMAGE_UPLOAD_CHUNK) {
    if (len == 0 || len > IMAGE_UPLOAD_CHUNK_BYTES || len > pss->image_size - pss->image_received) {
      fail(pss, "Image exceeds its declared size");
      return;
    }
    size_t header_bytes = pss->image_received < sizeof(pss->image_header)
                              ? sizeof(pss->image_header) - pss->image_received : 0;
    if (header_bytes > len) header_bytes = len;
    if (header_bytes > 0) memcpy(pss->image_header + pss->image_received, data, header_bytes);
    size_t offset = 0;
    while (offset < len) {
      uv_fs_t req;
      uv_buf_t buffer = uv_buf_init((char *)data + offset, (unsigned int)(len - offset));
      int written = uv_fs_write(NULL, &req, pss->image_fd, &buffer, 1, -1, NULL);
      uv_fs_req_cleanup(&req);
      if (written <= 0) {
        fail(pss, "Could not write the image to disk");
        return;
      }
      offset += written;
    }
    pss->image_received += len;
    lws_set_timer_usecs(pss->wsi, 30 * LWS_USEC_PER_SEC);
    reply(pss, "chunk", NULL);
    return;
  }
  if (len != 0 || pss->image_received != pss->image_size || !valid_header(pss)) {
    fail(pss, "Incomplete image or unsupported image contents");
    return;
  }
  uv_fs_t req;
  int err = uv_fs_close(NULL, &req, pss->image_fd, NULL);
  uv_fs_req_cleanup(&req);
  pss->image_fd = -1;
  if (err < 0) {
    fail(pss, "Could not finish saving the image");
    return;
  }
  // Return a generated absolute path. The browser pastes it only after success;
  // image bytes are never written to the PTY and Enter is never synthesized.
  reply(pss, "complete", pss->image_path);
  free(pss->image_path);
  free(pss->image_directory);
  pss->image_path = pss->image_directory = NULL;
  pss->image_received = pss->image_size = 0;
  lws_set_timer_usecs(pss->wsi, LWS_SET_TIMER_USEC_CANCEL);
}
