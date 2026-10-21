/* AI-hint: Exercise the actual Wayland hover callback and reject duplicate motion. */
#include <assert.h>
#include <stdio.h>
#ifndef MIOS_LOOKING_GLASS_CLIENT_INPUT_SOURCE
#define MIOS_LOOKING_GLASS_CLIENT_INPUT_SOURCE "input.c"
#endif
#include MIOS_LOOKING_GLASS_CLIENT_INPUT_SOURCE
struct WaylandDSState wlWm;
static bool absolute;
static unsigned motions, positions;
static double sentX, sentY;
uint64_t app_mouseSeq(void) { return 0; }
void app_mouseTrace(const char *file, unsigned int line, const char *function,
    uint64_t seq, const char *format, ...) {}
bool lgInput_supports(LG_InputSupport support) { return absolute; }
void wlInputPointerMotion(WaylandInput *input, double x, double y) { ++positions; }
void app_handleMouseRelative(double x, double y, double rx, double ry)
{ ++motions; sentX=x; sentY=y; assert(x==rx && y==ry); }
int main(void)
{
  wlWm.motion.x=12; wlWm.motion.y=20;
  pointerMotionHandler(NULL,NULL,1,wl_fixed_from_double(15),wl_fixed_from_double(25));
  assert(positions==1 && motions==1 && sentX==3 && sentY==5);
  atomic_store(&wlWm.lockActive,true);
  pointerMotionHandler(NULL,NULL,2,wl_fixed_from_double(17),wl_fixed_from_double(28));
  assert(positions==2 && motions==1);
  atomic_store(&wlWm.lockActive,false); absolute=true;
  pointerMotionHandler(NULL,NULL,3,wl_fixed_from_double(22),wl_fixed_from_double(32));
  assert(positions==3 && motions==1);
  absolute=false;
  pointerMotionHandler(NULL,NULL,4,wl_fixed_from_double(20),wl_fixed_from_double(31));
  assert(positions==4 && motions==2 && sentX==-2 && sentY==-1);
  puts("Wayland hover: relative SPICE movement PASS; locked and absolute duplicate controls PASS");
  return 0;
}
