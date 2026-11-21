/*
 * capture.c - grab frames from a USB camera on QNX 8.0 and keep the newest
 * one as a JPEG file, for `python3 -m readtheroom --frame-file <path>`.
 *
 * ============================================================================
 * NOT YET COMPILED OR RUN ON QNX. Nobody has had the device. It has only been
 * syntax-checked on a laptop against hand-written stand-in headers, which
 * proves the C is well-formed and nothing about the real camera library.
 * Every place where the API use is not copied from a QNX sample is marked
 *     UNSURE:
 * Search for that word first if the build or the first run fails.
 * ============================================================================
 *
 * Build on the Pi (self-hosted), same form as the mjpeg_example README:
 *
 *     sudo apk add qnx-sf-base-dev libjpeg-turbo-dev
 *     clang qnx/capture.c -o qnx/capture -lcamapi -lturbojpeg
 *
 * Run (USB camera is unit 5 in the Pi 5 quick start image's sensor config):
 *
 *     qnx/capture -l -u 5                      # list formats, sizes, rates
 *     qnx/capture -u 5 -o /tmp/frame.jpg       # 1280x720, about 15 writes/s
 *     qnx/capture -u 5 -w 640 -h 480 -r 10 -n 20 -v   # 20 frames then exit
 *
 * Where each part comes from (all under
 * https://gitlab.com/qnx/projects/camera-projects/applications/):
 *
 *   mjpeg_example/mjpeg_example.c
 *     - camera_open() mode flags, camera_set_vf_property() for format, size
 *       and rate, camera_get_vf_property(), the channel + pulse set-up,
 *       camera_enable_viewfinder_event() / camera_enable_status_event(),
 *       camera_start_viewfinder(handle, NULL, NULL, NULL), the
 *       TimerTimeout() + MsgReceivePulse() wait, camera_get_viewfinder_buffers()
 *       / camera_return_buffer(), framedesc.jpeg.bufsize, the status handling,
 *       the listing of formats (-l), and the clean-up order.
 *   camera_example2_events/camera_example2_events.c
 *     - the framedesc.ycbycr / cbycry / nv12 field names (width, height,
 *       stride, uv_offset, uv_stride) and how rows are addressed.
 *   github.com/qnx/codelabs  mediapipe-camera-python  qnx_camera_helper.py
 *     - confirms the same open / enable event / start / get / return order
 *       from Python on the 8.0.5 image, and the NV12 size rule
 *       uv_offset + uv_stride * height / 2.
 *
 * What is NOT from a QNX sample:
 *   - Compressing YUYV / UYVY / NV12 to JPEG. The QNX samples only DEcode
 *     JPEG (tjDecompress2). The encoding here uses libjpeg-turbo's
 *     tjCompressFromYUVPlanes(), written from the libjpeg-turbo documentation.
 *   - Running the event loop on the main thread (the samples use a second
 *     thread so the main thread can wait for a key press).
 *   - The temp-file-then-rename write and the write-rate limit.
 */

#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

/* UNSURE: mjpeg_example.c does not include <sys/neutrino.h> itself; it gets
 * ChannelCreate(), MsgReceivePulse(), struct _pulse and SIGEV_PULSE_INIT
 * through <camera/camera_api.h>. Including it explicitly is normal QNX
 * practice and should be harmless. If the compiler objects, delete this line. */
#include <sys/neutrino.h>

#include <camera/camera_api.h>
#include <turbojpeg.h>

/* Pulse codes: copied from mjpeg_example.c */
#define PULSE_CODE_DATA     (_PULSE_CODE_MINAVAIL + 1)
#define PULSE_CODE_STATUS   (_PULSE_CODE_MINAVAIL + 2)

/* Maximum wait for one pulse: must be longer than one frame period
 * (mjpeg_example.c uses 2000 ms). */
#define PULSE_TIMEOUT_NS    (2000ULL * 1000000ULL)

/* Give up after this many waits in a row with no frame, so that a wrapper
 * loop (see docs/qnx-bringup.md) can restart us. */
#define MAX_TIMEOUTS        5

static volatile sig_atomic_t g_stop = 0;

static void onSignal(int sig)
{
    (void)sig;
    g_stop = 1;
}

typedef struct {
    const char*     outPath;
    char            tmpPath[1024];
    int             quality;
    bool            verbose;
    tjhandle        tj;
    unsigned char*  jpegBuf;        /* allocated and grown by libjpeg-turbo */
    unsigned long   jpegSize;
    unsigned char*  planes;         /* scratch for planar YUV */
    size_t          planesSize;
    uint64_t        received;
    uint64_t        written;
    uint64_t        bad;
} capture_t;

/* ---- names for frame types: copied from mjpeg_example.c (shortened) ------ */
static const char* frametypeName(camera_frametype_t frametype)
{
    switch (frametype) {
    case CAMERA_FRAMETYPE_UNSPECIFIED:  return "unspecified";
    case CAMERA_FRAMETYPE_NV12:         return "nv12";
    case CAMERA_FRAMETYPE_NV21:         return "nv21";
    case CAMERA_FRAMETYPE_RGB8888:      return "rgb8888";
    case CAMERA_FRAMETYPE_BGR8888:      return "bgr8888";
    case CAMERA_FRAMETYPE_RGB888:       return "rgb888";
    case CAMERA_FRAMETYPE_GRAY8:        return "gray8";
    case CAMERA_FRAMETYPE_CBYCRY:       return "cbycry (UYVY)";
    case CAMERA_FRAMETYPE_YCBYCR:       return "ycbycr (YUYV)";
    case CAMERA_FRAMETYPE_RGB565:       return "rgb565";
    case CAMERA_FRAMETYPE_JPEG:         return "jpeg";
    default:                            return "other (see camera_api.h)";
    }
}

/* ---- -l: list what the camera offers. Copied from printCameraInfo() in
 * mjpeg_example.c with the error paths shortened. --------------------------- */
static int printCameraInfo(camera_unit_t unit)
{
    uint32_t            numTypes = 0;
    uint32_t            numRes = 0;
    uint32_t            numRates = 0;
    camera_handle_t     handle = CAMERA_HANDLE_INVALID;
    camera_frametype_t* types = NULL;
    camera_res_t*       res = NULL;
    double*             rates = NULL;
    bool                isMaxMin = false;
    camera_error_t      err;
    uint32_t            t, r, f;

    err = camera_open(unit, CAMERA_MODE_PREAD, &handle);
    if ((err != CAMERA_EOK) || (handle == CAMERA_HANDLE_INVALID)) {
        (void)fprintf(stderr, "Failed to open CAMERA_UNIT_%d: err=%d\n", (int)unit, err);
        return err;
    }
    err = camera_get_supported_vf_frame_types(handle, 0, &numTypes, NULL);
    if (err == CAMERA_EOK) {
        types = (camera_frametype_t*)calloc(numTypes ? numTypes : 1, sizeof(*types));
        err = (types == NULL) ? (camera_error_t)ENOMEM
            : camera_get_supported_vf_frame_types(handle, numTypes, &numTypes, types);
    }
    if (err != CAMERA_EOK) {
        (void)fprintf(stderr, "camera_get_supported_vf_frame_types failed: err=%d\n", err);
        free(types);
        (void)camera_close(handle);
        return err;
    }
    for (t = 0; t < numTypes; ++t) {
        (void)printf("%u. Format %s (number %d)\n", t + 1u, frametypeName(types[t]), (int)types[t]);
        err = camera_get_specified_vf_resolutions(handle, types[t], 0, &numRes, NULL);
        if (err != CAMERA_EOK) {
            (void)fprintf(stderr, "camera_get_specified_vf_resolutions failed: err=%d\n", err);
            continue;
        }
        res = (camera_res_t*)calloc(numRes ? numRes : 1, sizeof(*res));
        if (res == NULL) {
            break;
        }
        err = camera_get_specified_vf_resolutions(handle, types[t], numRes, &numRes, res);
        for (r = 0; (err == CAMERA_EOK) && (r < numRes); ++r) {
            (void)printf("\tResolution: %ux%u\n", (unsigned)res[r].width, (unsigned)res[r].height);
            if (camera_get_specified_vf_framerates(handle, types[t], res[r], 0, &numRates,
                                                   NULL, &isMaxMin) != CAMERA_EOK) {
                continue;
            }
            rates = (double*)calloc(numRates ? numRates : 1, sizeof(*rates));
            if (rates == NULL) {
                break;
            }
            if (camera_get_specified_vf_framerates(handle, types[t], res[r], numRates, &numRates,
                                                   rates, &isMaxMin) == CAMERA_EOK) {
                if (isMaxMin && (numRates >= 2u)) {
                    (void)printf("\t\tRange %5.2f fps to %5.2f fps\n", rates[0], rates[1]);
                } else {
                    for (f = 0; f < numRates; ++f) {
                        (void)printf("\t\t%5.2f fps\n", rates[f]);
                    }
                }
            }
            free(rates);
            rates = NULL;
        }
        free(res);
        res = NULL;
    }
    free(types);
    return camera_close(handle);
}

/* ---- file output: NOT from a sample --------------------------------------
 * The reader (readtheroom/camera.py FileFrameSource) must never see half a
 * file, so write next to the target and rename over it. rename() within one
 * directory replaces the target in one step on a POSIX filesystem.
 * UNSURE: this holds for /tmp on the quick start image only if /tmp is a real
 * filesystem. Do not point -o at /dev/shmem: the QNX guide says it is "not a
 * full filesystem", so rename may not behave. */
static int writeAtomic(capture_t* cap, const unsigned char* data, size_t size)
{
    int     fd;
    size_t  done = 0;

    fd = open(cap->tmpPath, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd == -1) {
        (void)fprintf(stderr, "Cannot open %s: %s\n", cap->tmpPath, strerror(errno));
        return errno;
    }
    while (done < size) {
        ssize_t n = write(fd, data + done, size - done);
        if (n == -1) {
            if (errno == EINTR) {
                continue;
            }
            (void)fprintf(stderr, "Cannot write %s: %s\n", cap->tmpPath, strerror(errno));
            (void)close(fd);
            return errno;
        }
        done += (size_t)n;
    }
    if (close(fd) == -1) {
        return errno;
    }
    if (rename(cap->tmpPath, cap->outPath) == -1) {
        (void)fprintf(stderr, "Cannot rename to %s: %s\n", cap->outPath, strerror(errno));
        return errno;
    }
    return EOK;
}

static unsigned char* scratch(capture_t* cap, size_t size)
{
    if (cap->planesSize < size) {
        unsigned char* grown = (unsigned char*)realloc(cap->planes, size);
        if (grown == NULL) {
            return NULL;
        }
        cap->planes = grown;
        cap->planesSize = size;
    }
    return cap->planes;
}

/* ---- YUV -> JPEG: NOT from a QNX sample -----------------------------------
 * UNSURE (whole function): written from the libjpeg-turbo documentation for
 * tjCompressFromYUVPlanes(). Planar Y, U, V go in; subsamp says how the
 * colour planes are reduced (TJSAMP_422 for YUYV/UYVY, TJSAMP_420 for NV12). */
static int compressPlanes(capture_t* cap, unsigned char* y, unsigned char* u, unsigned char* v,
                          int width, int height, int subsamp)
{
    const unsigned char*    planes[3];
    int                     strides[3];

    planes[0] = y;
    planes[1] = u;
    planes[2] = v;
    strides[0] = width;
    strides[1] = width / 2;
    strides[2] = width / 2;
    if (tjCompressFromYUVPlanes(cap->tj, planes, width, strides, height, subsamp,
                                &cap->jpegBuf, &cap->jpegSize, cap->quality,
                                TJFLAG_FASTDCT) == -1) {
        (void)fprintf(stderr, "tjCompressFromYUVPlanes() error: %s\n", tjGetErrorStr());
        return EINVAL;
    }
    return writeAtomic(cap, cap->jpegBuf, (size_t)cap->jpegSize);
}

/* Packed 4:2:2. Row addressing (framebuf + row * stride, 2 * width bytes per
 * row) is from processCameraData() in camera_example2_events.c.
 * yFirst = true for YCBYCR (Y Cb Y Cr), false for CBYCRY (Cb Y Cr Y). */
static int handlePacked422(capture_t* cap, const uint8_t* buf, uint32_t width, uint32_t height,
                           uint32_t stride, bool yFirst)
{
    size_t          ySize = (size_t)width * height;
    size_t          cSize = (size_t)(width / 2u) * height;
    unsigned char*  y;
    unsigned char*  u;
    unsigned char*  v;
    uint32_t        row, i;

    if ((width == 0u) || (height == 0u) || ((width % 2u) != 0u) || (stride < 2u * width)) {
        (void)fprintf(stderr, "Odd 4:2:2 frame: %ux%u stride %u\n", width, height, stride);
        return EINVAL;
    }
    y = scratch(cap, ySize + 2u * cSize);
    if (y == NULL) {
        return ENOMEM;
    }
    u = y + ySize;
    v = u + cSize;
    for (row = 0; row < height; ++row) {
        const uint8_t* line = buf + (size_t)row * stride;
        for (i = 0; i < width / 2u; ++i) {
            const uint8_t* px = line + 4u * i;
            if (yFirst) {
                *y++ = px[0]; *u++ = px[1]; *y++ = px[2]; *v++ = px[3];
            } else {
                *u++ = px[0]; *y++ = px[1]; *v++ = px[2]; *y++ = px[3];
            }
        }
    }
    return compressPlanes(cap, cap->planes, cap->planes + ySize, cap->planes + ySize + cSize,
                          (int)width, (int)height, TJSAMP_422);
}

/* NV12: a Y plane, then one plane of interleaved U,V at half size both ways.
 * Field names and addressing from camera_example2_events.c; the same layout
 * is what qnx_camera_helper.py checks before it converts. */
static int handleNv12(capture_t* cap, const uint8_t* buf, uint32_t width, uint32_t height,
                      uint32_t stride, int64_t uvOffset, int64_t uvStride)
{
    size_t          ySize = (size_t)width * height;
    size_t          cSize = (size_t)(width / 2u) * (height / 2u);
    unsigned char*  y;
    unsigned char*  u;
    unsigned char*  v;
    uint32_t        row, i;

    if ((width == 0u) || (height == 0u) || ((width % 2u) != 0u) || ((height % 2u) != 0u) ||
        (stride < width) || (uvStride < (int64_t)width) ||
        (uvOffset < (int64_t)stride * (int64_t)height)) {
        (void)fprintf(stderr, "Odd NV12 frame: %ux%u stride %u uv_offset %" PRId64
                      " uv_stride %" PRId64 "\n", width, height, stride, uvOffset, uvStride);
        return EINVAL;
    }
    y = scratch(cap, ySize + 2u * cSize);
    if (y == NULL) {
        return ENOMEM;
    }
    u = y + ySize;
    v = u + cSize;
    for (row = 0; row < height; ++row) {
        (void)memcpy(y + (size_t)row * width, buf + (size_t)row * stride, width);
    }
    for (row = 0; row < height / 2u; ++row) {
        const uint8_t* line = buf + uvOffset + (int64_t)row * uvStride;
        for (i = 0; i < width / 2u; ++i) {
            *u++ = line[2u * i];
            *v++ = line[2u * i + 1u];
        }
    }
    return compressPlanes(cap, cap->planes, cap->planes + ySize, cap->planes + ySize + cSize,
                          (int)width, (int)height, TJSAMP_420);
}

/* MJPEG from the camera: the bytes are already a JPEG file, write them as-is.
 * buffer->framebuf and framedesc.jpeg.bufsize are what handleJpegFrame() in
 * mjpeg_example.c passes to the decoder. That sample notes "Currently some
 * received JPEG frames are invalid", so check the start and end markers and
 * drop anything else; the Python side would reject it anyway. */
static int handleJpeg(capture_t* cap, const uint8_t* buf, uint64_t size)
{
    while ((size > 4u) && (buf[size - 1u] == 0x00u)) {
        --size;                         /* cameras often pad the end with zeros */
    }
    if ((size < 4u) || (buf[0] != 0xFFu) || (buf[1] != 0xD8u) ||
        (buf[size - 2u] != 0xFFu) || (buf[size - 1u] != 0xD9u)) {
        return EINVAL;
    }
    return writeAtomic(cap, buf, (size_t)size);
}

static int handleFrame(capture_t* cap, camera_buffer_t* buffer)
{
    switch (buffer->frametype) {
    case CAMERA_FRAMETYPE_JPEG:
        return handleJpeg(cap, buffer->framebuf, buffer->framedesc.jpeg.bufsize);
    case CAMERA_FRAMETYPE_YCBYCR:
        return handlePacked422(cap, buffer->framebuf, buffer->framedesc.ycbycr.width,
                               buffer->framedesc.ycbycr.height,
                               buffer->framedesc.ycbycr.stride, true);
    case CAMERA_FRAMETYPE_CBYCRY:
        return handlePacked422(cap, buffer->framebuf, buffer->framedesc.cbycry.width,
                               buffer->framedesc.cbycry.height,
                               buffer->framedesc.cbycry.stride, false);
    case CAMERA_FRAMETYPE_NV12:
        return handleNv12(cap, buffer->framebuf, buffer->framedesc.nv12.width,
                          buffer->framedesc.nv12.height, buffer->framedesc.nv12.stride,
                          buffer->framedesc.nv12.uv_offset, buffer->framedesc.nv12.uv_stride);
    default:
        (void)fprintf(stderr, "Frame type %d (%s) is not handled. Run with -l and pick "
                      "jpeg, ycbycr, cbycry or nv12 with -t.\n",
                      (int)buffer->frametype, frametypeName(buffer->frametype));
        return ENOTSUP;
    }
}

/* Status pulses: copied from handleStatusPulse() in mjpeg_example.c, with the
 * frame-statistics calls left out. Only used to print what the camera says. */
static void handleStatus(camera_handle_t handle, struct _pulse pulse)
{
    camera_devstatus_t  status;
    uint16_t            extra;

    if (camera_get_status_details(handle, pulse.value, &status, &extra) != CAMERA_EOK) {
        return;
    }
    switch (status) {
    case CAMERA_STATUS_DISCONNECTED:
        (void)fprintf(stderr, "camera status: DISCONNECTED (cable? power?)\n");
        break;
    case CAMERA_STATUS_VIEWFINDER_ERROR:
        (void)fprintf(stderr, "camera status: VIEWFINDER_ERROR\n");
        break;
    case CAMERA_STATUS_VIEWFINDER_ACTIVE:
        (void)fprintf(stderr, "camera status: streaming\n");
        break;
    default:
        break;
    }
}

static double nowSeconds(void)
{
    struct timespec ts;

    (void)clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static void usage(void)
{
    (void)printf(
        "capture - keep the newest camera frame as a JPEG file\n"
        "  -u unit     camera unit (default 5 = USB camera on the Pi 5 quick start image)\n"
        "  -o path     output file (default /tmp/frame.jpg)\n"
        "  -w width -h height   size to ask for (default 1280x720; 0 0 = leave as is)\n"
        "  -f rate     camera frame rate to ask for (default: leave as is)\n"
        "  -r rate     most files written per second (default 15)\n"
        "  -t type     auto | jpeg | ycbycr | cbycry | nv12 | keep (default auto:\n"
        "              try jpeg, otherwise keep what the sensor config gives)\n"
        "  -q quality  JPEG quality when this program compresses (default 85)\n"
        "  -n count    stop after writing this many frames (default: run forever)\n"
        "  -l          list formats, sizes and rates, then exit\n"
        "  -v          print a line every 5 seconds\n");
}

int main(int argc, char* argv[])
{
    camera_unit_t       unit = (camera_unit_t)5;
    camera_handle_t     handle = CAMERA_HANDLE_INVALID;
    camera_frametype_t  frametype = CAMERA_FRAMETYPE_UNSPECIFIED;
    camera_eventkey_t   dataKey = -1;
    camera_eventkey_t   statusKey = -1;
    camera_buffer_t     buffer;
    struct sigevent     dataEvent;
    struct sigevent     statusEvent;
    struct _pulse       pulse;
    struct sigaction    action;
    capture_t           cap;
    const char*         typeArg = "auto";
    uint32_t            width = 1280;
    uint32_t            height = 720;
    double              framerate = 0.0;
    double              writeRate = 15.0;
    double              lastWrite = 0.0;
    double              lastReport;
    uint64_t            lastWritten = 0;
    long                maxFrames = 0;
    bool                listOnly = false;
    bool                started = false;
    int                 timeouts = 0;
    int                 chid = -1;
    int                 coid = -1;
    int                 err;
    int                 opt;
    int                 rc = EXIT_FAILURE;

    (void)memset(&cap, 0, sizeof(cap));
    cap.outPath = "/tmp/frame.jpg";
    cap.quality = 85;

    while ((opt = getopt(argc, argv, "f:h:ln:o:q:r:t:u:vw:")) != -1) {
        switch (opt) {
        case 'f': framerate = strtod(optarg, NULL); break;
        case 'h': height = (uint32_t)strtol(optarg, NULL, 10); break;
        case 'l': listOnly = true; break;
        case 'n': maxFrames = strtol(optarg, NULL, 10); break;
        case 'o': cap.outPath = optarg; break;
        case 'q': cap.quality = (int)strtol(optarg, NULL, 10); break;
        case 'r': writeRate = strtod(optarg, NULL); break;
        case 't': typeArg = optarg; break;
        case 'u': unit = (camera_unit_t)strtol(optarg, NULL, 10); break;
        case 'v': cap.verbose = true; break;
        case 'w': width = (uint32_t)strtol(optarg, NULL, 10); break;
        default:  usage(); return EXIT_FAILURE;
        }
    }
    if ((unit < CAMERA_UNIT_1) || (unit > CAMERA_UNIT_64)) {
        (void)fprintf(stderr, "Camera unit must be between 1 and 64\n");
        return EXIT_FAILURE;
    }
    if (listOnly) {
        return (printCameraInfo(unit) == CAMERA_EOK) ? EXIT_SUCCESS : EXIT_FAILURE;
    }
    if ((cap.quality < 1) || (cap.quality > 100) || (writeRate <= 0.0) ||
        ((width == 0u) != (height == 0u))) {
        usage();
        return EXIT_FAILURE;
    }
    if (snprintf(cap.tmpPath, sizeof(cap.tmpPath), "%s.tmp", cap.outPath)
            >= (int)sizeof(cap.tmpPath)) {
        (void)fprintf(stderr, "Output path is too long\n");
        return EXIT_FAILURE;
    }

    (void)memset(&action, 0, sizeof(action));
    action.sa_handler = onSignal;       /* no SA_RESTART: the wait must wake up */
    (void)sigaction(SIGINT, &action, NULL);
    (void)sigaction(SIGTERM, &action, NULL);

    /* -- Open the camera: mode flags copied from startApp() in mjpeg_example.c.
     * PWRITE is what lets us change format, size and rate. */
    err = camera_open(unit,
                      (uint32_t)CAMERA_MODE_DREAD | (uint32_t)CAMERA_MODE_PREAD |
                      (uint32_t)CAMERA_MODE_PWRITE,
                      &handle);
    if ((err != CAMERA_EOK) || (handle == CAMERA_HANDLE_INVALID)) {
        (void)fprintf(stderr,
                      "Failed to open CAMERA_UNIT_%d: err=%d\n"
                      "Check: is the sensor service running (pidin ar | grep sensor)?\n"
                      "       does /dev/sensor/camera%d exist? is another program using it?\n",
                      (int)unit, err, (int)unit);
        return EXIT_FAILURE;
    }

    /* -- Pick the frame type: call copied from mjpeg_example.c. That sample's
     * own error text says JPEG needs "create_window = false" and
     * "memory_allocator_type = from_sensor" in the sensor config, which is the
     * "USB camera MJPEG format" alternative for SENSOR_UNIT_5. */
    if ((strcmp(typeArg, "auto") == 0) || (strcmp(typeArg, "jpeg") == 0)) {
        err = camera_set_vf_property(handle, CAMERA_IMGPROP_FORMAT, CAMERA_FRAMETYPE_JPEG);
        if ((err != CAMERA_EOK) && (strcmp(typeArg, "jpeg") == 0)) {
            (void)fprintf(stderr,
                          "The camera unit refused JPEG (err=%d). Run -l to see whether jpeg is\n"
                          "listed, and switch SENSOR_UNIT_%d to the MJPEG alternative in the\n"
                          "sensor config (see docs/qnx-bringup.md).\n", err, (int)unit);
            goto done;
        }
        if (err != CAMERA_EOK) {
            (void)fprintf(stderr, "JPEG not available (err=%d); using the unit's own format.\n",
                          err);
        }
    } else if (strcmp(typeArg, "keep") != 0) {
        camera_frametype_t wanted;

        if (strcmp(typeArg, "ycbycr") == 0) {
            wanted = CAMERA_FRAMETYPE_YCBYCR;
        } else if (strcmp(typeArg, "cbycry") == 0) {
            wanted = CAMERA_FRAMETYPE_CBYCRY;
        } else if (strcmp(typeArg, "nv12") == 0) {
            wanted = CAMERA_FRAMETYPE_NV12;
        } else {
            usage();
            goto done;
        }
        err = camera_set_vf_property(handle, CAMERA_IMGPROP_FORMAT, wanted);
        if (err != CAMERA_EOK) {
            (void)fprintf(stderr, "The camera unit refused format %s: err=%d\n", typeArg, err);
            goto done;
        }
    }

    /* -- Size and rate: calls copied from mjpeg_example.c. A refusal is not
     * fatal here; we carry on with whatever the unit is set to and say so. */
    if ((width != 0u) && (height != 0u)) {
        err = camera_set_vf_property(handle, CAMERA_IMGPROP_WIDTH, width,
                                     CAMERA_IMGPROP_HEIGHT, height);
        if (err != CAMERA_EOK) {
            (void)fprintf(stderr, "Could not set %ux%u (err=%d); run -l for the sizes on offer.\n",
                          width, height, err);
        }
    }
    if (framerate > 0.0) {
        err = camera_set_vf_property(handle, CAMERA_IMGPROP_FRAMERATE, framerate);
        if (err != CAMERA_EOK) {
            (void)fprintf(stderr, "Could not set %.2f fps (err=%d); run -l for the rates on "
                          "offer.\n", framerate, err);
        }
    }
    err = camera_get_vf_property(handle,
                                 CAMERA_IMGPROP_FORMAT, &frametype,
                                 CAMERA_IMGPROP_WIDTH, &width,
                                 CAMERA_IMGPROP_HEIGHT, &height,
                                 CAMERA_IMGPROP_FRAMERATE, &framerate);
    if (err != CAMERA_EOK) {
        (void)fprintf(stderr, "Failed to read back the camera settings: err=%d\n", err);
        goto done;
    }
    (void)printf("Camera unit %d: %s %ux%u %.2f fps -> %s (at most %.1f files/s)\n",
                 (int)unit, frametypeName(frametype), width, height, framerate,
                 cap.outPath, writeRate);
    (void)fflush(stdout);

    if (frametype != CAMERA_FRAMETYPE_JPEG) {
        cap.tj = tjInitCompress();
        if (cap.tj == NULL) {
            (void)fprintf(stderr, "tjInitCompress() error: %s\n", tjGetErrorStr());
            goto done;
        }
    }

    /* -- Channel and pulses: copied from eventProcessingThread() in
     * mjpeg_example.c (same calls, same order, same arguments). */
    chid = ChannelCreate(_NTO_CHF_PRIVATE);
    if (chid < 0) {
        (void)fprintf(stderr, "Failed to create a channel: err=%d\n", errno);
        goto done;
    }
    coid = ConnectAttach(0, 0, chid, _NTO_SIDE_CHANNEL, 0);
    if (coid < 0) {
        (void)fprintf(stderr, "Failed to attach to the channel: err=%d\n", errno);
        goto done;
    }
    SIGEV_PULSE_INIT(&dataEvent, coid, -1, PULSE_CODE_DATA, 0u);
    SIGEV_PULSE_INIT(&statusEvent, coid, -1, PULSE_CODE_STATUS, 0u);
    err = camera_enable_viewfinder_event(handle, CAMERA_EVENTMODE_READONLY, &dataKey, &dataEvent);
    if (err != CAMERA_EOK) {
        (void)fprintf(stderr, "Failed to enable the frame event: err=%d\n", err);
        goto done;
    }
    err = camera_enable_status_event(handle, &statusKey, &statusEvent);
    if (err != CAMERA_EOK) {
        (void)fprintf(stderr, "Failed to enable the status event (carrying on): err=%d\n", err);
        statusKey = -1;
    }

    /* -- Start streaming: copied from mjpeg_example.c. NULL callbacks = event
     * mode, frames are announced by pulses. */
    err = camera_start_viewfinder(handle, NULL, NULL, NULL);
    if (err != CAMERA_EOK) {
        (void)fprintf(stderr, "Failed to start CAMERA_UNIT_%d: err=%d\n", (int)unit, err);
        goto done;
    }
    started = true;
    lastReport = nowSeconds();

    while (g_stop == 0) {
        uint64_t timeoutNs = PULSE_TIMEOUT_NS;

        /* Wait for a pulse, at most 2 s: copied from mjpeg_example.c. */
        if (TimerTimeout(CLOCK_MONOTONIC, _NTO_TIMEOUT_RECEIVE, NULL, &timeoutNs, NULL) == -1) {
            (void)fprintf(stderr, "Failed to set the wait timer: err=%d\n", errno);
            break;
        }
        if (MsgReceivePulse(chid, &pulse, sizeof(pulse), NULL) == -1) {
            if (errno == EINTR) {
                continue;               /* a signal: the loop condition decides */
            }
            if (errno == ETIMEDOUT) {
                ++timeouts;
                (void)fprintf(stderr, "No frame for 2 s (%d of %d)\n", timeouts, MAX_TIMEOUTS);
                if (timeouts >= MAX_TIMEOUTS) {
                    break;
                }
                continue;
            }
            (void)fprintf(stderr, "Failed to receive a pulse: err=%d\n", errno);
            break;
        }

        if (pulse.code == PULSE_CODE_STATUS) {
            handleStatus(handle, pulse);
            continue;
        }
        if (pulse.code != PULSE_CODE_DATA) {
            continue;
        }
        timeouts = 0;

        /* Take the frame: copied from handleJpegFrame() in mjpeg_example.c.
         * Every buffer taken must be given back with camera_return_buffer(). */
        err = camera_get_viewfinder_buffers(handle, dataKey, &buffer, NULL);
        if (err != CAMERA_EOK) {
            continue;
        }
        ++cap.received;
        {
            double now = nowSeconds();

            if (now - lastWrite >= (1.0 / writeRate) - 0.002) {
                err = handleFrame(&cap, &buffer);
                if (err == EOK) {
                    ++cap.written;
                    lastWrite = now;
                } else {
                    ++cap.bad;
                }
            } else {
                err = EOK;
            }
            (void)camera_return_buffer(handle, &buffer);
            if (err == ENOTSUP) {
                break;                  /* a format we cannot use: no point looping */
            }
            if (cap.verbose && (now - lastReport >= 5.0)) {
                (void)printf("received %" PRIu64 ", written %" PRIu64 " (%.1f/s), unusable %"
                             PRIu64 "\n", cap.received, cap.written,
                             (double)(cap.written - lastWritten) / (now - lastReport), cap.bad);
                (void)fflush(stdout);
                lastReport = now;
                lastWritten = cap.written;
            }
        }
        if ((maxFrames > 0) && (cap.written >= (uint64_t)maxFrames)) {
            break;
        }
    }
    if ((g_stop != 0) || ((maxFrames > 0) && (cap.written >= (uint64_t)maxFrames))) {
        rc = EXIT_SUCCESS;
    }
    (void)printf("Stopping: received %" PRIu64 ", written %" PRIu64 ", unusable %" PRIu64 "\n",
                 cap.received, cap.written, cap.bad);

done:
    /* Clean-up order copied from stopApp() and the end of
     * eventProcessingThread() in mjpeg_example.c: stop the stream, disable the
     * events, drop the connection and channel, close the camera. */
    if (started) {
        (void)camera_stop_viewfinder(handle);
    }
    if (dataKey != -1) {
        (void)camera_disable_event(handle, dataKey);
    }
    if (statusKey != -1) {
        (void)camera_disable_event(handle, statusKey);
    }
    if (coid != -1) {
        (void)ConnectDetach(coid);
    }
    if (chid != -1) {
        (void)ChannelDestroy(chid);
    }
    if (cap.tj != NULL) {
        (void)tjDestroy(cap.tj);
    }
    if (cap.jpegBuf != NULL) {
        tjFree(cap.jpegBuf);
    }
    free(cap.planes);
    (void)camera_close(handle);
    (void)unlink(cap.tmpPath);
    return rc;
}
