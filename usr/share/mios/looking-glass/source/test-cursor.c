/* AI-hint: Cursor metadata bounds and BGRA/RGBA conversion regression controls. */
#include <assert.h>
#include <stdio.h>
#ifndef MIOS_LOOKING_GLASS_PIPEWIRE_SOURCE
#define MIOS_LOOKING_GLASS_PIPEWIRE_SOURCE "pipewire.c"
#endif
#include MIOS_LOOKING_GLASS_PIPEWIRE_SOURCE
static unsigned char out[64];
static CapturePointer posted;
static int posts;
static bool get(void ** data, uint32_t * capacity)
{ *data = out; *capacity = sizeof(out); return true; }
static void post(const CapturePointer * p) { posted = *p; ++posts; }
int main(void)
{
  struct pipewire state = {.getPointerBuffer = get, .postPointerBuffer = post};
  this = &state;
  unsigned char payload[sizeof(struct spa_meta_cursor) + sizeof(struct spa_meta_bitmap) + 16] = {0};
  struct spa_meta_cursor * c = (void *)payload;
  c->id = 1; c->position.x = 100; c->position.y = 200;
  c->bitmap_offset = sizeof(*c);
  struct spa_meta_bitmap * b = (void *)(payload + sizeof(*c));
  b->format = SPA_VIDEO_FORMAT_BGRA; b->size.width = b->size.height = 2;
  b->stride = 8; b->offset = sizeof(*b);
  memset((unsigned char *)b + b->offset, 0x5a, 16);
  struct spa_meta meta = {.type = SPA_META_Cursor, .size = sizeof(payload), .data = payload};
  struct spa_buffer buffer = {.n_metas = 1, .metas = &meta};
  postCursor(&buffer);
  assert(posts == 1 && posted.positionUpdate && posted.shapeUpdate);
  assert(posted.x == 100 && posted.y == 200 && posted.pitch == 8 && out[15] == 0x5a);
  memset(out, 0xa5, sizeof(out));
  c->bitmap_offset = UINT32_MAX; postCursor(&buffer);
  assert(posts == 2 && !posted.shapeUpdate && out[0] == 0xa5);
  c->bitmap_offset = sizeof(*c); b->offset = UINT32_MAX; postCursor(&buffer);
  assert(posts == 3 && !posted.shapeUpdate && out[0] == 0xa5);
  b->offset = sizeof(*b); b->stride = -8; postCursor(&buffer);
  assert(posts == 4 && !posted.shapeUpdate && out[0] == 0xa5);
  b->stride = 8; b->size.height = 513; postCursor(&buffer);
  assert(posts == 5 && !posted.shapeUpdate && out[0] == 0xa5);
  c->id = 0; postCursor(&buffer); assert(posts == 5);
  c->id = 1; meta.size = sizeof(*c) - 1; postCursor(&buffer); assert(posts == 5);
  buffer.n_metas = 0; postCursor(&buffer); assert(posts == 5);
  buffer.n_metas = 1; meta.size = sizeof(payload);
  b->size.height = 2; b->format = SPA_VIDEO_FORMAT_RGBA;
  unsigned char * rgba = (unsigned char *)b + b->offset;
  for (int i = 0; i < 16; i += 4)
  { rgba[i] = 0x11; rgba[i+1] = 0x22; rgba[i+2] = 0x33; rgba[i+3] = 0x44; }
  c->hotspot.x = 1; c->hotspot.y = 1;
  postCursor(&buffer);
  assert(posts == 6 && posted.shapeUpdate && posted.hx == 1 && posted.hy == 1);
  assert(out[0] == 0x33 && out[1] == 0x22 && out[2] == 0x11 && out[3] == 0x44);
  assert(out[15] == 0x44 && out[16] == 0xa5);
  puts("cursor metadata: BGRA copy, KDE RGBA conversion, and 7 malformed/missing controls PASS");
  return 0;
}
