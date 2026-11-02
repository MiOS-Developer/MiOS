// AI-hint: Wayland pass-through between a nested compositor and its parent that changes ONLY linux-dmabuf feedback:
// main_device / tranche_target_device become the chosen render node, and (linear_only) the format table keeps only
// DRM_FORMAT_MOD_LINEAR entries, the one layout two different GPUs both read. Aquamarine's Wayland backend has no
// render-node selector: it opens the main device the parent announces (and libdrm resolves that dev_t by scanning
// /dev/dri, so a bind-mounted alias cannot redirect it). Everything else -- bytes and fds -- is forwarded unchanged.
// AI-related: tools/native/mios-gen/src/nested_session.rs, tools/native/mios-gen/src/gpu_topology.rs,
//   usr/share/doc/mios/desktop-gpu-routing.md, linux-dmabuf-v1.xml (zwp_linux_dmabuf_feedback_v1)

use rustix::net::{
    recvmsg, sendmsg, RecvAncillaryBuffer, RecvAncillaryMessage, RecvFlags, SendAncillaryBuffer,
    SendAncillaryMessage, SendFlags,
};
use std::{
    collections::{HashMap, VecDeque},
    io::{self, IoSlice, IoSliceMut, Write},
    mem::MaybeUninit,
    net::Shutdown,
    os::{
        fd::{AsFd, OwnedFd},
        unix::{
            fs::FileExt,
            net::{UnixListener, UnixStream},
        },
    },
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
    thread,
};

const LINEAR: u64 = 0;

#[derive(Clone, Copy, PartialEq, Debug)]
enum Kind {
    Registry,
    Seat,
    Keyboard,
    Dmabuf,
    Feedback,
}

#[derive(Default)]
struct Objects {
    kinds: HashMap<u32, Kind>,
    /// Per feedback object: old format-table index -> new index (None = dropped).
    remap: HashMap<u32, Vec<Option<u16>>>,
}

pub struct Rewrite {
    /// st_rdev of the render node the nested compositor must open.
    pub rdev: u64,
    pub linear_only: bool,
    /// Feedback events rewritten / format tables filtered (for the session descriptor and tests).
    pub devices_rewritten: AtomicU64,
    pub tables_filtered: AtomicU64,
}

fn u32_at(b: &[u8], o: usize) -> Option<u32> {
    b.get(o..o + 4)
        .map(|s| u32::from_ne_bytes(s.try_into().unwrap()))
}

fn pad4(n: usize) -> usize {
    (n + 3) & !3
}

/// The string argument at `o`: (text without NUL, offset after it).
fn string_at(b: &[u8], o: usize) -> Option<(&str, usize)> {
    let len = u32_at(b, o)? as usize;
    let s = b.get(o + 4..o + 4 + len)?;
    let text = std::str::from_utf8(s.strip_suffix(&[0]).unwrap_or(s)).ok()?;
    Some((text, o + 4 + pad4(len)))
}

/// Track the objects whose events carry fds or get rewritten. Requests: wl_display.get_registry, wl_registry.bind,
/// wl_seat.get_keyboard, zwp_linux_dmabuf_v1.get_default_feedback / get_surface_feedback.
fn note_request(objs: &mut Objects, id: u32, op: u16, args: &[u8]) {
    let kind = objs.kinds.get(&id).copied();
    let new = |o| u32_at(args, o);
    match (id, kind, op) {
        (1, _, 1) => {
            if let Some(n) = new(0) {
                objs.kinds.insert(n, Kind::Registry);
            }
        }
        (_, Some(Kind::Registry), 0) => {
            let Some((iface, o)) = string_at(args, 4) else {
                return;
            };
            let Some(n) = new(o + 4) else { return };
            match iface {
                "wl_seat" => objs.kinds.insert(n, Kind::Seat),
                "zwp_linux_dmabuf_v1" => objs.kinds.insert(n, Kind::Dmabuf),
                _ => None,
            };
        }
        (_, Some(Kind::Seat), 1) => {
            if let Some(n) = new(0) {
                objs.kinds.insert(n, Kind::Keyboard);
            }
        }
        (_, Some(Kind::Dmabuf), 2 | 3) => {
            if let Some(n) = new(0) {
                objs.kinds.insert(n, Kind::Feedback);
            }
        }
        _ => {}
    }
}

fn message(id: u32, op: u16, args: &[u8]) -> Vec<u8> {
    let mut m = Vec::with_capacity(8 + args.len());
    m.extend_from_slice(&id.to_ne_bytes());
    m.extend_from_slice(&((((8 + args.len()) as u32) << 16) | op as u32).to_ne_bytes());
    m.extend_from_slice(args);
    m
}

fn array_arg(data: &[u8]) -> Vec<u8> {
    let mut a = (data.len() as u32).to_ne_bytes().to_vec();
    a.extend_from_slice(data);
    a.resize(4 + pad4(data.len()), 0);
    a
}

/// The format table keeping only LINEAR entries: (new table fd, new size, old index -> new index).
fn filter_table(fd: &OwnedFd, size: u32) -> Option<(OwnedFd, u32, Vec<Option<u16>>)> {
    let file = std::fs::File::from(fd.try_clone().ok()?);
    let mut table = vec![0u8; size as usize];
    file.read_exact_at(&mut table, 0).ok()?;
    let mut kept = Vec::new();
    let mut map = Vec::with_capacity(table.len() / 16);
    for e in table.chunks_exact(16) {
        let modifier = u64::from_ne_bytes(e[8..16].try_into().unwrap());
        if modifier == LINEAR && kept.len() / 16 < u16::MAX as usize {
            map.push(Some((kept.len() / 16) as u16));
            kept.extend_from_slice(e);
        } else {
            map.push(None);
        }
    }
    if kept.is_empty() {
        // The parent lists no LINEAR modifier at all (NVIDIA's Hyprland table: block-linear + INVALID only). Offer
        // LINEAR for every format it lists: the nested GPU can render it and the parent's driver may import it.
        let mut formats: Vec<u32> = Vec::new();
        map.clear();
        for e in table.chunks_exact(16) {
            let f = u32::from_ne_bytes(e[0..4].try_into().unwrap());
            let i = formats.iter().position(|x| *x == f).unwrap_or_else(|| {
                formats.push(f);
                formats.len() - 1
            });
            map.push(u16::try_from(i).ok());
        }
        for f in &formats {
            kept.extend_from_slice(&f.to_ne_bytes());
            kept.extend_from_slice(&[0; 4]);
            kept.extend_from_slice(&LINEAR.to_ne_bytes());
        }
        if kept.is_empty() {
            return None;
        }
    }
    let memfd =
        rustix::fs::memfd_create("mios-dmabuf-linear-table", rustix::fs::MemfdFlags::CLOEXEC)
            .ok()?;
    let mut out = std::fs::File::from(memfd);
    out.write_all(&kept).ok()?;
    Some((OwnedFd::from(out), kept.len() as u32, map))
}

/// Rewrite one event; `fds` is the queue of fds received from the parent, in order.
fn rewrite_event(
    objs: &mut Objects,
    rw: &Rewrite,
    id: u32,
    op: u16,
    args: &[u8],
    fds: &mut VecDeque<OwnedFd>,
    out_fds: &mut Vec<OwnedFd>,
) -> Vec<u8> {
    let kind = objs.kinds.get(&id).copied();
    match (id, kind, op) {
        (1, _, 1) => {
            if let Some(gone) = u32_at(args, 0) {
                objs.kinds.remove(&gone);
                objs.remap.remove(&gone);
            }
        }
        (_, Some(Kind::Keyboard), 0) => out_fds.extend(fds.pop_front()),
        (_, Some(Kind::Feedback), 1) => {
            let Some(fd) = fds.pop_front() else {
                return message(id, op, args);
            };
            let size = u32_at(args, 0).unwrap_or(0);
            if rw.linear_only {
                if let Some((table, new_size, map)) = filter_table(&fd, size) {
                    rw.tables_filtered.fetch_add(1, Ordering::Relaxed);
                    objs.remap.insert(id, map);
                    out_fds.push(table);
                    return message(id, op, &new_size.to_ne_bytes());
                }
            }
            out_fds.push(fd);
        }
        (_, Some(Kind::Feedback), 2 | 4) if u32_at(args, 0) == Some(8) => {
            rw.devices_rewritten.fetch_add(1, Ordering::Relaxed);
            return message(id, op, &array_arg(&rw.rdev.to_ne_bytes()));
        }
        (_, Some(Kind::Feedback), 5) => {
            if let (Some(map), Some(len)) = (objs.remap.get(&id), u32_at(args, 0)) {
                let idx = args.get(4..4 + len as usize).unwrap_or_default();
                let mut seen = std::collections::HashSet::new();
                let kept: Vec<u8> = idx
                    .chunks_exact(2)
                    .filter_map(|c| {
                        map.get(u16::from_ne_bytes([c[0], c[1]]) as usize)
                            .copied()
                            .flatten()
                    })
                    .filter(|i| seen.insert(*i))
                    .flat_map(u16::to_ne_bytes)
                    .collect();
                return message(id, op, &array_arg(&kept));
            }
        }
        _ => {}
    }
    message(id, op, args)
}

fn recv(sock: &UnixStream, buf: &mut [u8], fds: &mut VecDeque<OwnedFd>) -> io::Result<usize> {
    let mut space = [MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(64))];
    let mut cmsg = RecvAncillaryBuffer::new(&mut space);
    loop {
        match recvmsg(
            sock,
            &mut [IoSliceMut::new(buf)],
            &mut cmsg,
            RecvFlags::CMSG_CLOEXEC,
        ) {
            Ok(r) => {
                for m in cmsg.drain() {
                    if let RecvAncillaryMessage::ScmRights(it) = m {
                        fds.extend(it);
                    }
                }
                return Ok(r.bytes);
            }
            Err(rustix::io::Errno::INTR) => continue,
            Err(e) => return Err(e.into()),
        }
    }
}

/// Send `data` with `fds` attached to its first byte (libwayland sends at most 28 fds per message batch).
fn send(sock: &UnixStream, data: &[u8], fds: Vec<OwnedFd>) -> io::Result<()> {
    let mut off = 0;
    let mut fds = Some(fds).filter(|f| !f.is_empty());
    while off < data.len() || fds.is_some() {
        let held = fds.take().unwrap_or_default();
        let borrowed: Vec<_> = held.iter().map(|f| f.as_fd()).collect();
        let mut space =
            vec![MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(borrowed.len().max(1)))];
        let mut cmsg = SendAncillaryBuffer::new(&mut space);
        if !borrowed.is_empty() && !cmsg.push(SendAncillaryMessage::ScmRights(&borrowed)) {
            return Err(io::Error::other("too many fds for one message"));
        }
        match sendmsg(
            sock,
            &[IoSlice::new(&data[off..])],
            &mut cmsg,
            SendFlags::NOSIGNAL,
        ) {
            Ok(n) => off += n,
            Err(rustix::io::Errno::INTR) => {
                fds = Some(held).filter(|f| !f.is_empty());
                continue;
            }
            Err(e) => return Err(e.into()),
        }
        if data.is_empty() {
            break;
        }
    }
    Ok(())
}

/// Complete messages at the front of `pending`: (id, opcode, args range) and the bytes consumed.
fn split(pending: &[u8]) -> Result<(Vec<(u32, u16, std::ops::Range<usize>)>, usize), String> {
    let mut msgs = Vec::new();
    let mut o = 0;
    while pending.len() - o >= 8 {
        let id = u32_at(pending, o).unwrap();
        let w = u32_at(pending, o + 4).unwrap();
        let size = (w >> 16) as usize;
        if size < 8 || size % 4 != 0 {
            return Err(format!(
                "malformed Wayland message (object {id}, size {size})"
            ));
        }
        if pending.len() - o < size {
            break;
        }
        msgs.push((id, (w & 0xffff) as u16, o + 8..o + size));
        o += size;
    }
    Ok((msgs, o))
}

fn pump_requests(from: &UnixStream, to: &UnixStream, objs: &Mutex<Objects>) -> Result<(), String> {
    let mut buf = vec![0u8; 1 << 16];
    let mut pending = Vec::new();
    loop {
        let mut fds = VecDeque::new();
        let n = recv(from, &mut buf, &mut fds).map_err(|e| e.to_string())?;
        if n == 0 && fds.is_empty() {
            return Ok(());
        }
        pending.extend_from_slice(&buf[..n]);
        let (msgs, used) = split(&pending)?;
        {
            let mut o = objs.lock().unwrap();
            for (id, op, r) in msgs {
                note_request(&mut o, id, op, &pending[r]);
            }
        }
        pending.drain(..used);
        // Requests are never changed: the received bytes and fds go on exactly as they came.
        send(to, &buf[..n], fds.into_iter().collect()).map_err(|e| e.to_string())?;
    }
}

fn pump_events(
    from: &UnixStream,
    to: &UnixStream,
    objs: &Mutex<Objects>,
    rw: &Rewrite,
) -> Result<(), String> {
    let mut buf = vec![0u8; 1 << 16];
    let mut pending = Vec::new();
    let mut fds = VecDeque::new();
    let mut out = Vec::new();
    let mut out_fds = Vec::new();
    loop {
        let n = recv(from, &mut buf, &mut fds).map_err(|e| e.to_string())?;
        if n == 0 && fds.is_empty() {
            return Ok(());
        }
        pending.extend_from_slice(&buf[..n]);
        let (msgs, used) = split(&pending)?;
        {
            let mut o = objs.lock().unwrap();
            for (id, op, r) in msgs {
                let m = rewrite_event(&mut o, rw, id, op, &pending[r], &mut fds, &mut out_fds);
                out.extend_from_slice(&m);
            }
        }
        pending.drain(..used);
        // An fd this proxy does not know the owner of goes out with the bytes it arrived with. Only when a message is
        // cut in half while fds wait (its own fd may be among them) does the proxy read on before writing.
        if !pending.is_empty() && !fds.is_empty() {
            continue;
        }
        out_fds.extend(fds.drain(..));
        if !out.is_empty() || !out_fds.is_empty() {
            send(to, &out, std::mem::take(&mut out_fds)).map_err(|e| e.to_string())?;
            out.clear();
        }
    }
}

fn connection(client: UnixStream, server: UnixStream, rw: Arc<Rewrite>) -> io::Result<()> {
    let objs = Arc::new(Mutex::new(Objects::default()));
    let (c2, s2, o2) = (client.try_clone()?, server.try_clone()?, objs.clone());
    thread::spawn(move || {
        if let Err(e) = pump_requests(&c2, &s2, &o2) {
            eprintln!("mios-gen wl-device-proxy: requests: {e}");
        }
        let _ = s2.shutdown(Shutdown::Both);
        let _ = c2.shutdown(Shutdown::Both);
    });
    thread::spawn(move || {
        if let Err(e) = pump_events(&server, &client, &objs, &rw) {
            eprintln!("mios-gen wl-device-proxy: events: {e}");
        }
        let _ = client.shutdown(Shutdown::Both);
        let _ = server.shutdown(Shutdown::Both);
    });
    Ok(())
}

/// Serve `listener`: every connection is paired with a new connection to `upstream` (the parent's socket).
pub fn serve(
    listener: UnixListener,
    upstream: PathBuf,
    rw: Arc<Rewrite>,
) -> thread::JoinHandle<()> {
    thread::spawn(move || {
        for conn in listener.incoming() {
            let Ok(client) = conn else { break };
            match UnixStream::connect(&upstream) {
                Ok(server) => {
                    if let Err(e) = connection(client, server, rw.clone()) {
                        eprintln!("mios-gen wl-device-proxy: {e}");
                    }
                }
                Err(e) => eprintln!(
                    "mios-gen wl-device-proxy: parent {}: {e}",
                    upstream.display()
                ),
            }
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn arg_u32(v: u32) -> Vec<u8> {
        v.to_ne_bytes().to_vec()
    }
    fn arg_str(s: &str) -> Vec<u8> {
        let mut b = arg_u32(s.len() as u32 + 1);
        b.extend_from_slice(s.as_bytes());
        b.push(0);
        b.resize(4 + pad4(s.len() + 1), 0);
        b
    }

    /// A fake parent and a fake nested client on socket pairs around the real proxy threads.
    #[test]
    fn feedback_main_device_and_format_table_are_rewritten_and_other_traffic_is_untouched() {
        let dir = tempfile::tempdir().unwrap();
        let parent_path = dir.path().join("parent");
        let parent = UnixListener::bind(&parent_path).unwrap();
        let proxy_path = dir.path().join("proxy");
        let rw = Arc::new(Rewrite {
            rdev: 0xe280,
            linear_only: true,
            devices_rewritten: AtomicU64::new(0),
            tables_filtered: AtomicU64::new(0),
        });
        let _h = serve(
            UnixListener::bind(&proxy_path).unwrap(),
            parent_path,
            rw.clone(),
        );
        let client = UnixStream::connect(&proxy_path).unwrap();
        let (server, _) = parent.accept().unwrap();
        let mut sbuf = vec![0u8; 4096];
        // Client: get_registry(2), bind(name 7, zwp_linux_dmabuf_v1 v4 -> 3), get_default_feedback(4), bind wl_seat -> 5, get_keyboard(6).
        let mut req = message(1, 1, &arg_u32(2));
        req.extend(message(
            2,
            0,
            &[
                arg_u32(7),
                arg_str("zwp_linux_dmabuf_v1"),
                arg_u32(4),
                arg_u32(3),
            ]
            .concat(),
        ));
        req.extend(message(3, 2, &arg_u32(4)));
        req.extend(message(
            2,
            0,
            &[arg_u32(8), arg_str("wl_seat"), arg_u32(9), arg_u32(5)].concat(),
        ));
        req.extend(message(5, 1, &arg_u32(6)));
        send(&client, &req, vec![]).unwrap();
        let mut got = Vec::new();
        while got.len() < req.len() {
            let n = recv(&server, &mut sbuf, &mut VecDeque::new()).unwrap();
            got.extend_from_slice(&sbuf[..n]);
        }
        assert_eq!(got, req, "requests pass through unchanged");
        // Parent: keymap fd on the keyboard, format table (2 entries: AMD-tiled, LINEAR), main device 0xe281, tranche
        // formats [0, 1], an unrelated event on object 9.
        let keymap = rustix::fs::memfd_create("keymap", rustix::fs::MemfdFlags::CLOEXEC).unwrap();
        std::fs::File::from(keymap.try_clone().unwrap())
            .write_all(b"xkb")
            .unwrap();
        let table = rustix::fs::memfd_create("table", rustix::fs::MemfdFlags::CLOEXEC).unwrap();
        let mut t = Vec::new();
        for (fmt, m) in [
            (0x34325258u32, 0x0200_0000_0000_0001u64),
            (0x34325258, LINEAR),
        ] {
            t.extend_from_slice(&fmt.to_ne_bytes());
            t.extend_from_slice(&[0; 4]);
            t.extend_from_slice(&m.to_ne_bytes());
        }
        std::fs::File::from(table.try_clone().unwrap())
            .write_all(&t)
            .unwrap();
        let mut ev = message(6, 0, &[arg_u32(1), arg_u32(3)].concat());
        ev.extend(message(4, 1, &arg_u32(32)));
        ev.extend(message(4, 2, &array_arg(&0xe281u64.to_ne_bytes())));
        ev.extend(message(
            4,
            5,
            &array_arg(&[0u16.to_ne_bytes(), 1u16.to_ne_bytes()].concat()),
        ));
        ev.extend(message(9, 0, &arg_u32(42)));
        send(&server, &ev, vec![keymap, table]).unwrap();
        let mut cfds = VecDeque::new();
        let mut got = Vec::new();
        while split(&got).map(|(m, _)| m.len()).unwrap_or(0) < 5 {
            let n = recv(&client, &mut sbuf, &mut cfds).unwrap();
            got.extend_from_slice(&sbuf[..n]);
        }
        let (msgs, _) = split(&got).unwrap();
        let body = |i: usize| &got[msgs[i].2.clone()];
        assert_eq!((msgs[0].0, msgs[0].1), (6, 0));
        assert_eq!(cfds.len(), 2, "keymap and the filtered table both arrive");
        let mut km = [0u8; 3];
        std::fs::File::from(cfds.pop_front().unwrap())
            .read_exact_at(&mut km, 0)
            .unwrap();
        assert_eq!(&km, b"xkb", "the keymap fd is the parent's own");
        assert_eq!(u32_at(body(1), 0), Some(16), "table keeps one LINEAR entry");
        let mut nt = [0u8; 16];
        std::fs::File::from(cfds.pop_front().unwrap())
            .read_exact_at(&mut nt, 0)
            .unwrap();
        assert_eq!(u64::from_ne_bytes(nt[8..16].try_into().unwrap()), LINEAR);
        assert_eq!(
            body(2),
            &array_arg(&0xe280u64.to_ne_bytes())[..],
            "main device = the chosen render node"
        );
        assert_eq!(
            body(3),
            &array_arg(&0u16.to_ne_bytes())[..],
            "tranche index remapped: LINEAR (old 1) is new 0"
        );
        assert_eq!(
            (msgs[4].0, body(4)),
            (9, &arg_u32(42)[..]),
            "unrelated events unchanged"
        );
        assert_eq!(rw.devices_rewritten.load(Ordering::Relaxed), 1);
        assert_eq!(rw.tables_filtered.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn a_table_without_linear_gets_linear_per_format() {
        let table = rustix::fs::memfd_create("t", rustix::fs::MemfdFlags::CLOEXEC).unwrap();
        let mut t = Vec::new();
        for (fmt, m) in [
            (1u32, 0x0300_0000_0000_0011u64),
            (2, 0x0300_0000_0000_0011),
            (1, 0x00ff_ffff_ffff_ffff),
        ] {
            t.extend_from_slice(&fmt.to_ne_bytes());
            t.extend_from_slice(&[0; 4]);
            t.extend_from_slice(&m.to_ne_bytes());
        }
        std::fs::File::from(table.try_clone().unwrap())
            .write_all(&t)
            .unwrap();
        let (fd, size, map) = filter_table(&table, 48).unwrap();
        assert_eq!(size, 32, "two formats, one LINEAR entry each");
        assert_eq!(map, vec![Some(0), Some(1), Some(0)]);
        let mut nt = [0u8; 32];
        std::fs::File::from(fd).read_exact_at(&mut nt, 0).unwrap();
        assert_eq!(u64::from_ne_bytes(nt[8..16].try_into().unwrap()), LINEAR);
        assert_eq!(u32::from_ne_bytes(nt[16..20].try_into().unwrap()), 2);
    }

    #[test]
    fn negative_unknown_objects_and_deleted_ids_are_not_rewritten() {
        let rw = Rewrite {
            rdev: 1,
            linear_only: true,
            devices_rewritten: AtomicU64::new(0),
            tables_filtered: AtomicU64::new(0),
        };
        let mut o = Objects::default();
        let dev = array_arg(&5u64.to_ne_bytes());
        // Object 4 was never created as a feedback: its opcode-2 event passes unchanged.
        let m = rewrite_event(&mut o, &rw, 4, 2, &dev, &mut VecDeque::new(), &mut vec![]);
        assert_eq!(m, message(4, 2, &dev));
        o.kinds.insert(4, Kind::Feedback);
        assert_ne!(
            rewrite_event(&mut o, &rw, 4, 2, &dev, &mut VecDeque::new(), &mut vec![]),
            message(4, 2, &dev)
        );
        // wl_display.delete_id(4): the id may be reused for anything; it is forgotten.
        rewrite_event(
            &mut o,
            &rw,
            1,
            1,
            &arg_u32(4),
            &mut VecDeque::new(),
            &mut vec![],
        );
        assert_eq!(
            rewrite_event(&mut o, &rw, 4, 2, &dev, &mut VecDeque::new(), &mut vec![]),
            message(4, 2, &dev)
        );
        assert!(
            split(&message(1, 0, &[])[..4]).unwrap().0.is_empty(),
            "a partial header waits"
        );
        let mut bad = message(1, 0, &[]);
        bad[4..8].copy_from_slice(&(4u32 << 16).to_ne_bytes());
        assert!(
            split(&bad).is_err(),
            "a message smaller than its header is rejected"
        );
    }
}
