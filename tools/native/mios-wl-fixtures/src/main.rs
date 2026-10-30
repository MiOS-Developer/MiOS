// AI-hint: mios-subsurface-churn: an xdg toplevel (app_id mios-subsurface-churn) that repaints at <fps> and, every
//          <life> frames, destroys its subsurface (wl_subsurface and its wl_surface) and creates a new one, so a focused
//          window keeps churning subsurfaces. The mios-pacing window meter must read the toplevel's rate and keep
//          watches only for the surfaces that still exist (`hyprctl mios-pacing window` -> "watched"). Bounded: it
//          exits after <seconds> (at most 600) and prints `frames= created= destroyed=`. Test-only (never installed).
// AI-related: usr/share/mios/hyprland/plugins/mios-pacing/main.cpp, usr/share/mios/hyprland/plugins/mios-pacing/pacing.hpp,
// AI-related: tests/test-window-fps-live.py, tools/native/mios-wl-fixtures/Cargo.toml
// AI-functions: main, run, buffer, parse_args

#![forbid(unsafe_code)]

use std::io::Write;
use std::os::fd::AsFd;
use std::process::ExitCode;
use std::time::{Duration, Instant};

use wayland_client::globals::{registry_queue_init, GlobalListContents};
use wayland_client::protocol::{
    wl_buffer, wl_compositor, wl_registry, wl_shm, wl_shm_pool, wl_subcompositor, wl_subsurface, wl_surface,
};
use wayland_client::{delegate_noop, Connection, Dispatch, QueueHandle};
use wayland_protocols::xdg::shell::client::{xdg_surface, xdg_toplevel, xdg_wm_base};

#[derive(Default)]
struct State {
    configured: bool,
    closed: bool,
}

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
    fn event(_: &mut Self, _: &wl_registry::WlRegistry, _: wl_registry::Event, _: &GlobalListContents, _: &Connection, _: &QueueHandle<Self>) {}
}
impl Dispatch<xdg_wm_base::XdgWmBase, ()> for State {
    fn event(_: &mut Self, wm: &xdg_wm_base::XdgWmBase, ev: xdg_wm_base::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        if let xdg_wm_base::Event::Ping { serial } = ev {
            wm.pong(serial);
        }
    }
}
impl Dispatch<xdg_surface::XdgSurface, ()> for State {
    fn event(st: &mut Self, s: &xdg_surface::XdgSurface, ev: xdg_surface::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        if let xdg_surface::Event::Configure { serial } = ev {
            s.ack_configure(serial);
            st.configured = true;
        }
    }
}
impl Dispatch<xdg_toplevel::XdgToplevel, ()> for State {
    fn event(st: &mut Self, _: &xdg_toplevel::XdgToplevel, ev: xdg_toplevel::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        if let xdg_toplevel::Event::Close = ev {
            st.closed = true;
        }
    }
}
delegate_noop!(State: ignore wl_shm::WlShm);
delegate_noop!(State: ignore wl_buffer::WlBuffer);
delegate_noop!(State: ignore wl_surface::WlSurface);
delegate_noop!(State: wl_compositor::WlCompositor);
delegate_noop!(State: wl_subcompositor::WlSubcompositor);
delegate_noop!(State: wl_subsurface::WlSubsurface);
delegate_noop!(State: wl_shm_pool::WlShmPool);

/// A `w`x`h` ARGB8888 shm buffer filled with `argb` (written through the memfd: no mapping, no unsafe).
fn buffer(shm: &wl_shm::WlShm, qh: &QueueHandle<State>, w: i32, h: i32, argb: u32) -> Result<wl_buffer::WlBuffer, String> {
    let fd = rustix::fs::memfd_create("mios-subsurface-churn", rustix::fs::MemfdFlags::CLOEXEC).map_err(|e| format!("memfd_create: {e}"))?;
    let mut file = std::fs::File::from(fd);
    let px: Vec<u8> = std::iter::repeat_n(argb.to_le_bytes(), (w * h) as usize).flatten().collect();
    file.write_all(&px).map_err(|e| format!("memfd write: {e}"))?;
    let pool = shm.create_pool(file.as_fd(), w * h * 4, qh, ());
    let b = pool.create_buffer(0, w, h, w * 4, wl_shm::Format::Argb8888, qh, ());
    pool.destroy();
    Ok(b)
}

fn parse_args(args: &[String]) -> Result<(f64, u64, f64), String> {
    let [fps, life, secs] = args else {
        return Err("usage: mios-subsurface-churn <fps> <life_frames> <seconds>".into());
    };
    let fps: f64 = fps.parse().map_err(|_| format!("bad fps {fps:?}"))?;
    let life: u64 = life.parse().map_err(|_| format!("bad life_frames {life:?}"))?;
    let secs: f64 = secs.parse().map_err(|_| format!("bad seconds {secs:?}"))?;
    if !(fps > 0.0 && fps <= 1000.0) || life == 0 || !(secs > 0.0 && secs <= 600.0) {
        return Err("fps in (0, 1000], life_frames >= 1, seconds in (0, 600]".into());
    }
    Ok((fps, life, secs))
}

fn run(fps: f64, life: u64, secs: f64) -> Result<(u64, u64, u64), String> {
    let conn = Connection::connect_to_env().map_err(|e| format!("Wayland connect: {e}"))?;
    let (globals, mut queue) = registry_queue_init::<State>(&conn).map_err(|e| format!("Wayland registry: {e}"))?;
    let qh = queue.handle();
    let compositor: wl_compositor::WlCompositor = globals.bind(&qh, 4..=6, ()).map_err(|e| format!("wl_compositor: {e}"))?;
    let subcompositor: wl_subcompositor::WlSubcompositor = globals.bind(&qh, 1..=1, ()).map_err(|e| format!("wl_subcompositor: {e}"))?;
    let shm: wl_shm::WlShm = globals.bind(&qh, 1..=1, ()).map_err(|e| format!("wl_shm: {e}"))?;
    let wm: xdg_wm_base::XdgWmBase = globals.bind(&qh, 1..=6, ()).map_err(|e| format!("xdg_wm_base: {e}"))?;
    let mut st = State::default();

    let top = compositor.create_surface(&qh, ());
    let xs = wm.get_xdg_surface(&top, &qh, ());
    let tl = xs.get_toplevel(&qh, ());
    tl.set_app_id("mios-subsurface-churn".into());
    tl.set_title("mios-subsurface-churn".into());
    top.commit();
    while !st.configured {
        queue.blocking_dispatch(&mut st).map_err(|e| format!("Wayland: {e}"))?;
    }

    let (w, h) = (320, 240);
    let tops = [buffer(&shm, &qh, w, h, 0xff20_3040)?, buffer(&shm, &qh, w, h, 0xff30_4050)?];
    let sub_buf = buffer(&shm, &qh, 64, 64, 0xffc0_8020)?;
    let mut sub: Option<(wl_subsurface::WlSubsurface, wl_surface::WlSurface)> = None;
    let (mut frames, mut created, mut destroyed) = (0u64, 0u64, 0u64);
    let period = Duration::from_secs_f64(1.0 / fps);
    let end = Instant::now() + Duration::from_secs_f64(secs);
    let mut next = Instant::now();
    while !st.closed && Instant::now() < end {
        if frames % life == 0 {
            if let Some((ss, s)) = sub.take() {
                ss.destroy();
                s.destroy();
                destroyed += 1;
            }
            let s = compositor.create_surface(&qh, ());
            let ss = subcompositor.get_subsurface(&s, &top, &qh, ());
            ss.set_position(16 + (created % 8) as i32 * 8, 16);
            ss.set_desync();
            s.attach(Some(&sub_buf), 0, 0);
            s.damage_buffer(0, 0, 64, 64);
            s.commit();
            created += 1;
            sub = Some((ss, s));
        }
        top.attach(Some(&tops[(frames & 1) as usize]), 0, 0);
        top.damage_buffer(0, 0, w, h);
        top.commit();
        frames += 1;
        queue.roundtrip(&mut st).map_err(|e| format!("Wayland: {e}"))?;
        next += period;
        let now = Instant::now();
        if next > now {
            std::thread::sleep(next - now);
        } else {
            next = now; // fell behind (a stalled compositor): resync, never burst
        }
    }
    Ok((frames, created, destroyed))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let (fps, life, secs) = match parse_args(&args) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("{e}");
            return ExitCode::from(2);
        }
    };
    match run(fps, life, secs) {
        Ok((frames, created, destroyed)) => {
            println!("frames={frames} created={created} destroyed={destroyed}");
            ExitCode::SUCCESS
        }
        Err(e) => {
            eprintln!("mios-subsurface-churn: {e}");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::parse_args;

    fn a(v: &[&str]) -> Vec<String> {
        v.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn arguments_are_bounded() {
        assert_eq!(parse_args(&a(&["30", "3", "10"])), Ok((30.0, 3, 10.0)));
        assert!(parse_args(&a(&["0", "3", "10"])).is_err());
        assert!(parse_args(&a(&["30", "0", "10"])).is_err());
        assert!(parse_args(&a(&["30", "3", "601"])).is_err(), "never unbounded");
        assert!(parse_args(&a(&["30", "3"])).is_err());
    }
}
