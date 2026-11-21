/* Stand-in for libxcb-cursor.so.0, which Qt 6 needs on Linux but which is not installed on this
 * machine (and cannot be downloaded here). It reports "no cursor theme", so Qt falls back to the
 * standard X cursors. Not needed on Windows, or on Linux systems that have libxcb-cursor0. */
#include <stdint.h>
typedef struct xcb_connection_t xcb_connection_t;
typedef struct xcb_screen_t xcb_screen_t;
typedef struct xcb_cursor_context_t xcb_cursor_context_t;
typedef uint32_t xcb_cursor_t;

int xcb_cursor_context_new(xcb_connection_t *conn, xcb_screen_t *screen, xcb_cursor_context_t **ctx) {
    (void)conn; (void)screen;
    if (ctx) *ctx = 0;
    return -1;
}

xcb_cursor_t xcb_cursor_load_cursor(xcb_cursor_context_t *ctx, const char *name) {
    (void)ctx; (void)name;
    return 0;
}

void xcb_cursor_context_free(xcb_cursor_context_t *ctx) { (void)ctx; }
