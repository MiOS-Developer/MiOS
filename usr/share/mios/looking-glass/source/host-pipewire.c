/* AI-hint: Verified complete source replacement for Linux capture and hover input.
 * AI-related: usr/share/mios/looking-glass/source/README.md
 */
/**
 * Looking Glass
 * Copyright © 2017-2026 The Looking Glass Authors
 * https://looking-glass.io
 *
 * This program is free software; you can redistribute it and/or modify it
 * under the terms of the GNU General Public License as published by the Free
 * Software Foundation; either version 2 of the License, or (at your option)
 * any later version.
 *
 * This program is distributed in the hope that it will be useful, but WITHOUT
 * ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 * FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 * more details.
 *
 * You should have received a copy of the GNU General Public License along
 * with this program; if not, write to the Free Software Foundation, Inc., 59
 * Temple Place, Suite 330, Boston, MA 02111-1307 USA
 */

#include "portal.h"
#include "interface/capture.h"
#include "interface/platform.h"
#include "common/util.h"
#include "common/debug.h"
#include "common/stringutils.h"
#include "common/option.h"
#include <string.h>
#include <stdlib.h>
#include <unistd.h>

#include <pipewire/pipewire.h>
#include <spa/pod/builder.h>
#include <spa/param/format.h>
#include <spa/param/video/format-utils.h>

struct pipewire
{
  struct Portal         * portal;
  char                  * sessionHandle;
  struct pw_thread_loop * threadLoop;
  struct pw_context     * context;
  struct pw_core        * core;
  struct spa_hook         coreListener;
  struct pw_stream      * stream;
  struct spa_hook         streamListener;

  CaptureGetPointerBuffer getPointerBuffer;
  CapturePostPointerBuffer postPointerBuffer;
  struct spa_buffer * pendingBuffer;
  bool pending, borrowed;
  bool          stop;
  bool          hasFormat;
  bool          formatChanged;
  int           width, height, dataHeight, pitch;
  CaptureFormat format;
  bool          hdr;
  bool          hdrPQ;
  uint8_t     * frameData;
  unsigned int  formatVer;
};

static struct pipewire * this = NULL;

// forwards

static bool pipewire_deinit(void);

// implementation

static const char * pipewire_getName(void)
{
  return "PipeWire";
}

static bool pipewire_create(
  CaptureGetPointerBuffer getPointerBufferFn,
  CapturePostPointerBuffer postPointerBufferFn,
  unsigned                 frameBuffers
)
{
  DEBUG_ASSERT(!this);
  pw_init(NULL, NULL);
  this = calloc(1, sizeof(*this));
  if (!this) return false;
  this->getPointerBuffer = getPointerBufferFn;
  this->postPointerBuffer = postPointerBufferFn;
  return true;
}

static void coreErrorCallback(void * opaque, uint32_t id, int seq, int res, const char * message)
{
  DEBUG_ERROR("pipewire error: id %" PRIu32 ", seq: %d, res: %d (%s): %s",
    id, seq, res, strerror(res), message);
}

static const struct pw_core_events coreEvents = {
  PW_VERSION_CORE_EVENTS,
  .error = coreErrorCallback,
};

static bool startStream(struct pw_stream * stream, uint32_t node)
{
  char buffer[1024];
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));
  const int targetFps = option_get_int("app", "throttleFPS");
  const int preferredFps = targetFps > 0 ? targetFps : 60;
  const int maximumFps = targetFps > 0 ? targetFps : 360;
  const struct spa_pod * param = spa_pod_builder_add_object(
    &builder, SPA_TYPE_OBJECT_Format, SPA_PARAM_EnumFormat,
    SPA_FORMAT_mediaType, SPA_POD_Id(SPA_MEDIA_TYPE_video),
    SPA_FORMAT_mediaSubtype, SPA_POD_Id(SPA_MEDIA_SUBTYPE_raw),
    SPA_FORMAT_VIDEO_format, SPA_POD_CHOICE_ENUM_Id(6,
      SPA_VIDEO_FORMAT_BGRA, SPA_VIDEO_FORMAT_RGBA,
      SPA_VIDEO_FORMAT_BGRx, SPA_VIDEO_FORMAT_RGBx,
      SPA_VIDEO_FORMAT_xBGR_210LE, SPA_VIDEO_FORMAT_RGBA_F16),
    SPA_FORMAT_VIDEO_size, SPA_POD_CHOICE_RANGE_Rectangle(
      &SPA_RECTANGLE(1920, 1080), &SPA_RECTANGLE(1, 1), &SPA_RECTANGLE(8192, 4320)),
    SPA_FORMAT_VIDEO_framerate, SPA_POD_CHOICE_RANGE_Fraction(
      &SPA_FRACTION(preferredFps, 1), &SPA_FRACTION(0, 1), &SPA_FRACTION(maximumFps, 1)));

  return pw_stream_connect(stream, PW_DIRECTION_INPUT, node,
    PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS, &param, 1) >= 0;
}

/* A queued buffer remains borrowed until the main capture thread accepts it.
 * Cursor publication happens there, after the shared-memory queues exist. */
static void streamProcessCallback(void * opaque)
{
  if (!this->hasFormat || this->stop) return;
  /* Cursor bitmaps are sent only when the shape changes. Do not discard
   * an older queued buffer that may carry the only copy of that shape. */
  struct pw_buffer * latest = pw_stream_dequeue_buffer(this->stream);
  if (!latest) return;
  struct spa_buffer * buffer = latest->buffer;
  this->pendingBuffer = buffer;
  this->frameData = buffer->n_datas && buffer->datas[0].chunk &&
      buffer->datas[0].chunk->size ? buffer->datas[0].data : NULL;
  this->pending = true;
  pw_thread_loop_signal(this->threadLoop, true);
  pw_stream_queue_buffer(this->stream, latest);
}

static void postCursor(struct spa_buffer * buffer)
{
  struct spa_meta * meta = spa_buffer_find_meta(buffer, SPA_META_Cursor);
  if (!meta || meta->size < sizeof(struct spa_meta_cursor)) return;
  const struct spa_meta_cursor * cursor = meta->data;
  if (!spa_meta_cursor_is_valid(cursor)) return;
  CapturePointer pointer = {
    .positionUpdate = true, .x = cursor->position.x,
    .y = cursor->position.y, .visible = true,
  };
  const size_t off = cursor->bitmap_offset;
  if (off >= sizeof(*cursor) && off <= meta->size &&
      meta->size - off >= sizeof(struct spa_meta_bitmap))
  {
    const struct spa_meta_bitmap * bitmap = (const void *)((const uint8_t *)cursor + off);
    if (bitmap->format && !bitmap->offset)
      pointer.visible = false;
    if ((bitmap->format == SPA_VIDEO_FORMAT_BGRA ||
         bitmap->format == SPA_VIDEO_FORMAT_RGBA) &&
        bitmap->offset >= sizeof(*bitmap) &&
        bitmap->size.width && bitmap->size.height &&
        bitmap->size.width <= 512 && bitmap->size.height <= 512 &&
        bitmap->stride >= (int64_t)bitmap->size.width * 4)
    {
      const size_t pixels = (size_t)bitmap->size.width * bitmap->size.height * 4;
      const size_t bytes = (size_t)bitmap->stride * bitmap->size.height;
      const size_t available = meta->size - off;
      void * dest = NULL;
      uint32_t capacity = 0;
      if (bitmap->offset <= available && bytes <= available - bitmap->offset &&
          this->getPointerBuffer(&dest, &capacity) && pixels <= capacity)
      {
        const uint8_t * src = (const uint8_t *)bitmap + bitmap->offset;
        const size_t pitch = (size_t)bitmap->size.width * 4;
        for (uint32_t row = 0; row < bitmap->size.height; ++row)
        {
          uint8_t * output = (uint8_t *)dest + row * pitch;
          const uint8_t * input = src + row * bitmap->stride;
          if (bitmap->format == SPA_VIDEO_FORMAT_BGRA)
            memcpy(output, input, pitch);
          else
            for (uint32_t col = 0; col < bitmap->size.width; ++col)
            {
              output[col * 4 + 0] = input[col * 4 + 2];
              output[col * 4 + 1] = input[col * 4 + 1];
              output[col * 4 + 2] = input[col * 4 + 0];
              output[col * 4 + 3] = input[col * 4 + 3];
            }
        }
        pointer.shapeUpdate = true;
        pointer.format = CAPTURE_FMT_COLOR;
        pointer.width = bitmap->size.width;
        pointer.height = bitmap->size.height;
        pointer.pitch = pitch;
        pointer.hx = cursor->hotspot.x < 0 ? 0 : cursor->hotspot.x;
        pointer.hy = cursor->hotspot.y < 0 ? 0 : cursor->hotspot.y;
      }
    }
  }
  this->postPointerBuffer(&pointer);
}

/* Called with the loop lock held, including after a frame was dropped. */
static void releaseBuffer(void)
{
  if (this->pending) pw_thread_loop_accept(this->threadLoop);
  this->pending = this->borrowed = false;
  this->pendingBuffer = NULL;
  this->frameData = NULL;
}

static CaptureFormat convertSpaFormat(enum spa_video_format spa)
{
  switch (spa)
  {
    case SPA_VIDEO_FORMAT_RGBA:
    case SPA_VIDEO_FORMAT_RGBx:
      return CAPTURE_FMT_RGBA;

    case SPA_VIDEO_FORMAT_BGRA:
    case SPA_VIDEO_FORMAT_BGRx:
      return CAPTURE_FMT_BGRA;

    case SPA_VIDEO_FORMAT_xBGR_210LE:
      return CAPTURE_FMT_RGBA10;

    case SPA_VIDEO_FORMAT_RGBA_F16:
      return CAPTURE_FMT_RGBA16F;

    default:
      return -1;
  }
}

static void streamParamChangedCallback(void * opaque, uint32_t id,
  const struct spa_pod * param)
{
  if (!param || id != SPA_PARAM_Format)
    return;

  uint32_t mediaType, mediaSubtype;
  if (spa_format_parse(param, &mediaType, &mediaSubtype) < 0 ||
      mediaType != SPA_MEDIA_TYPE_video ||
      mediaSubtype != SPA_MEDIA_SUBTYPE_raw)
    return;

  struct spa_video_info_raw info;
  if (spa_format_video_raw_parse(param, &info) < 0)
  {
    DEBUG_ERROR("Failed to parse video info");
    return;
  }

  this->width  = info.size.width;
  this->height = info.size.height;
  this->format = convertSpaFormat(info.format);
  this->hdr = info.format == SPA_VIDEO_FORMAT_xBGR_210LE ||
      info.format == SPA_VIDEO_FORMAT_RGBA_F16;
  this->hdrPQ  = true; // this is assumed and untested

  const int bpp = this->format == CAPTURE_FMT_RGBA16F ? 8 : 4;
  this->pitch = this->width * bpp;

  if (this->hasFormat)
  {
    this->formatChanged = true;
    return;
  }

  char buffer[1024];
  struct spa_pod_builder builder = SPA_POD_BUILDER_INIT(buffer, sizeof(buffer));

  param = spa_pod_builder_add_object(
    &builder, SPA_TYPE_OBJECT_ParamBuffers, SPA_PARAM_Buffers,
    SPA_PARAM_BUFFERS_dataType, SPA_POD_Int(1 << SPA_DATA_MemPtr));
  const struct spa_pod * params[2] = {param,
    spa_pod_builder_add_object(&builder, SPA_TYPE_OBJECT_ParamMeta, SPA_PARAM_Meta,
      SPA_PARAM_META_type, SPA_POD_Id(SPA_META_Cursor),
      SPA_PARAM_META_size, SPA_POD_CHOICE_RANGE_Int(
        sizeof(struct spa_meta_cursor) + sizeof(struct spa_meta_bitmap) + 64 * 64 * 4,
        sizeof(struct spa_meta_cursor) + sizeof(struct spa_meta_bitmap),
        sizeof(struct spa_meta_cursor) + sizeof(struct spa_meta_bitmap) + 512 * 512 * 4))};
  pw_stream_update_params(this->stream, params, 2);

  this->hasFormat = true;
  pw_thread_loop_signal(this->threadLoop, true);
}

static void streamStateChangedCallback(void * opaque,
  enum pw_stream_state oldState, enum pw_stream_state newState,
  const char * error)
{
  DEBUG_INFO("PipeWire stream state change: %s -> %s",
    pw_stream_state_as_string(oldState), pw_stream_state_as_string(newState));

  if (newState == PW_STREAM_STATE_ERROR)
    DEBUG_ERROR("PipeWire stream error: %s", error);
  if (newState == PW_STREAM_STATE_ERROR ||
      (newState == PW_STREAM_STATE_UNCONNECTED &&
       (oldState == PW_STREAM_STATE_STREAMING || oldState == PW_STREAM_STATE_PAUSED)))
  {
    this->stop = true;
    pw_thread_loop_signal(this->threadLoop, false);
  }
}

static const struct pw_stream_events streamEvents = {
  PW_VERSION_STREAM_EVENTS,
  .process       = streamProcessCallback,
  .state_changed = streamStateChangedCallback,
  .param_changed = streamParamChangedCallback,
};

static bool pipewire_init(void * ivshmemBase, unsigned * alignSize)
{
  DEBUG_ASSERT(this);
  this->stop = false;

  this->portal = portal_create();
  if (!this->portal)
  {
    DEBUG_ERROR("Failed to create xdg-desktop-portal for screencasting");
    goto fail;
  }

  if (!portal_createScreenCastSession(this->portal, &this->sessionHandle))
  {
    DEBUG_ERROR("Failed to create ScreenCast session");
    goto fail;
  }

  DEBUG_INFO("Got session handle: %s", this->sessionHandle);

  if (!portal_selectSource(this->portal, this->sessionHandle))
  {
    DEBUG_ERROR("Failed to select source");
    goto fail;
  }

  uint32_t pipewireNode = portal_getPipewireNode(this->portal, this->sessionHandle);
  if (!pipewireNode)
  {
    DEBUG_ERROR("Failed to get pipewire node");
    goto fail;
  }

  int pipewireFd = portal_openPipewireRemote(this->portal, this->sessionHandle);
  if (pipewireFd < 0)
  {
    DEBUG_ERROR("Failed to get pipewire fd");
    goto fail;
  }

  this->threadLoop = pw_thread_loop_new("lg-pipewire-capture", NULL);
  if (!this->threadLoop)
  {
    DEBUG_ERROR("Failed to create pipewire thread loop");
    close(pipewireFd);
    goto fail;
  }

  this->context = pw_context_new(pw_thread_loop_get_loop(this->threadLoop), NULL, 0);
  if (!this->context)
  {
    DEBUG_ERROR("Failed to create pipewire context");
    close(pipewireFd);
    goto fail;
  }

  if (pw_thread_loop_start(this->threadLoop) < 0)
  {
    DEBUG_ERROR("Failed to start pipewire thread loop");
    close(pipewireFd);
    goto fail;
  }

  pw_thread_loop_lock(this->threadLoop);

  this->core = pw_context_connect_fd(this->context, pipewireFd, NULL, 0);
  if (!this->core)
  {
    DEBUG_ERROR("Failed to create pipewire core: %s", strerror(errno));
    pw_thread_loop_unlock(this->threadLoop);
    close(pipewireFd);
    goto fail;
  }

  pw_core_add_listener(this->core, &this->coreListener, &coreEvents, NULL);

  this->stream = pw_stream_new(this->core, "Looking Glass (host)", pw_properties_new(
    PW_KEY_MEDIA_TYPE, "Video",
    PW_KEY_MEDIA_CATEGORY, "Capture",
    PW_KEY_MEDIA_ROLE, "Screen", NULL));
  if (!this->stream)
  {
    DEBUG_ERROR("Failed to create pipewire stream");
    pw_thread_loop_unlock(this->threadLoop);
    goto fail;
  }

  this->hasFormat     = false;
  this->formatChanged = false;
  this->frameData     = NULL;
  pw_stream_add_listener(this->stream, &this->streamListener, &streamEvents, NULL);

  if (!startStream(this->stream, pipewireNode))
  {
    DEBUG_ERROR("Failed to start pipewire stream");
    pw_thread_loop_unlock(this->threadLoop);
    goto fail;
  }

  while (!this->hasFormat)
    pw_thread_loop_wait(this->threadLoop);

  if (this->format < 0)
  {
    DEBUG_ERROR("Unknown frame format");
    pw_thread_loop_accept(this->threadLoop);
    pw_thread_loop_unlock(this->threadLoop);
    goto fail;
  }

  DEBUG_INFO("Frame size       : %dx%d", this->width, this->height);

  pw_thread_loop_accept(this->threadLoop);
  pw_thread_loop_unlock(this->threadLoop);

  return true;
fail:
  pipewire_deinit();
  return false;
}

static void pipewire_stop(void)
{
  if (!this->threadLoop) return;
  pw_thread_loop_lock(this->threadLoop);
  this->stop = true;
  releaseBuffer();
  pw_thread_loop_signal(this->threadLoop, false);
  pw_thread_loop_unlock(this->threadLoop);
}

static bool pipewire_deinit(void)
{
  if (this->threadLoop)
  {
    pw_thread_loop_lock(this->threadLoop);
    this->stop = true;
    releaseBuffer();
    pw_thread_loop_signal(this->threadLoop, false);
    pw_thread_loop_unlock(this->threadLoop);
    /* Finish the borrowed-buffer callback before destroying its stream. */
    pw_thread_loop_stop(this->threadLoop);
    pw_thread_loop_lock(this->threadLoop);
    if (this->stream)
    {
      pw_stream_disconnect(this->stream);
      pw_stream_destroy(this->stream);
      this->stream = NULL;
    }
    if (this->core)
    {
      pw_core_disconnect(this->core);
      this->core = NULL;
    }
    if (this->context)
    {
      pw_context_destroy(this->context);
      this->context = NULL;
    }
    pw_thread_loop_unlock(this->threadLoop);
    pw_thread_loop_destroy(this->threadLoop);
    this->threadLoop = NULL;
  }
  if (this->sessionHandle)
    portal_destroySession(this->portal, &this->sessionHandle);
  if (this->portal)
  {
    portal_free(this->portal);
    this->portal = NULL;
  }
  return true;
}

static void pipewire_free(void)
{
  DEBUG_ASSERT(this);
  pw_deinit();
  free(this);
  this = NULL;
}

static CaptureResult pipewire_capture(
  unsigned frameBufferIndex, KVMFRFrameBuffer * frame)
{
  pw_thread_loop_lock(this->threadLoop);
  if (this->borrowed) releaseBuffer();
  for (;;)
  {
    while (!this->pending && !this->stop)
    {
      int result = pw_thread_loop_timed_wait(this->threadLoop, 1);
      if (result == -ETIMEDOUT || result == ETIMEDOUT)
      {
        pw_thread_loop_unlock(this->threadLoop);
        return CAPTURE_RESULT_TIMEOUT;
      }
      if (result < 0)
      {
        pw_thread_loop_unlock(this->threadLoop);
        return CAPTURE_RESULT_ERROR;
      }
    }
    if (this->stop)
    {
      pw_thread_loop_unlock(this->threadLoop);
      return CAPTURE_RESULT_REINIT;
    }
    if (this->formatChanged)
    {
      ++this->formatVer;
      this->formatChanged = false;
    }
    postCursor(this->pendingBuffer);
    if (this->frameData) break;
    releaseBuffer();
  }
  this->borrowed = true;
  pw_thread_loop_unlock(this->threadLoop);
  return CAPTURE_RESULT_OK;
}

static CaptureResult pipewire_waitFrame(
  unsigned frameBufferIndex,
  CaptureFrame * frame,
  const size_t maxFrameSize)
{
  if (this->stop)
    return CAPTURE_RESULT_REINIT;

  const unsigned int maxHeight = maxFrameSize / this->pitch;
  this->dataHeight = min(maxHeight, this->height);

  frame->formatVer    = this->formatVer;
  frame->format       = this->format;
  frame->hdr          = this->hdr;
  frame->hdrPQ        = this->hdrPQ;
  frame->screenWidth  = this->width;
  frame->screenHeight = this->height;
  frame->dataWidth    = this->width;
  frame->dataHeight   = this->dataHeight;
  frame->frameWidth   = this->width;
  frame->frameHeight  = this->height;
  frame->truncated    = maxHeight < this->height;
  frame->pitch        = this->pitch;
  frame->stride       = this->width;
  frame->rotation     = CAPTURE_ROT_0;

  // TODO: implement damage.
  frame->damageRectsCount = 0;

  return CAPTURE_RESULT_OK;
}

static CaptureResult pipewire_getFrame(
  unsigned           frameBufferIndex,
  KVMFRFrameBuffer * frame,
  const size_t       maxFrameSize,
  CaptureFrame     * captureFrame)
{
  (void)captureFrame;
  pw_thread_loop_lock(this->threadLoop);
  CaptureResult result = CAPTURE_RESULT_REINIT;
  if (!this->stop && this->frameData)
  {
    const size_t size = (size_t)this->dataHeight * this->pitch;
    if (size <= maxFrameSize)
    {
      framebuffer_write(frame, this->frameData, size);
      result = CAPTURE_RESULT_OK;
    }
    else result = CAPTURE_RESULT_ERROR;
  }
  releaseBuffer();
  pw_thread_loop_unlock(this->threadLoop);
  return result;
}

struct CaptureInterface Capture_pipewire =
{
  .shortName       = "pipewire",
  .asyncCapture    = false,
  .preserveSession = true,
  .getName         = pipewire_getName,
  .create          = pipewire_create,
  .init            = pipewire_init,
  .stop            = pipewire_stop,
  .deinit          = pipewire_deinit,
  .free            = pipewire_free,
  .capture         = pipewire_capture,
  .waitFrame       = pipewire_waitFrame,
  .getFrame        = pipewire_getFrame
};
