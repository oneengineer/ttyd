#ifndef TTYD_IMAGE_UPLOAD_H
#define TTYD_IMAGE_UPLOAD_H

#define IMAGE_UPLOAD_MAX_BYTES (20 * 1024 * 1024)
#define IMAGE_UPLOAD_CHUNK_BYTES (64 * 1024)
#define IMAGE_UPLOAD_REPLY '3'
#define IMAGE_UPLOAD_BEGIN '4'
#define IMAGE_UPLOAD_CHUNK '5'
#define IMAGE_UPLOAD_FINISH '6'
#define IMAGE_UPLOAD_CANCEL '7'

struct pss_tty;

void image_upload_message(struct pss_tty *pss, char command, const char *data, size_t len);
void image_upload_abort(struct pss_tty *pss);
void image_upload_timeout(struct pss_tty *pss);

#endif
