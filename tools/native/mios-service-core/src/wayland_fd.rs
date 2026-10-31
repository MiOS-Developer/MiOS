// AI-hint: Wayland Unix socket descriptor passing helpers (safe abstractions around SCM_RIGHTS).
// AI-related: tools/native/mios-gen/src/dock.rs, usr/share/mios/quickshell/Sidebar.qml

use std::io;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};

/// Receives bytes and file descriptors sent over a UNIX domain stream socket using SCM_RIGHTS.
/// File descriptors are automatically wrapped in `OwnedFd` with `MSG_CMSG_CLOEXEC`.
pub fn recv_fds(fd: RawFd, buf: &mut [u8], fds: &mut Vec<OwnedFd>) -> io::Result<usize> {
    let mut cmsg = [0u8; 512];
    let mut iov = libc::iovec {
        iov_base: buf.as_mut_ptr().cast(),
        iov_len: buf.len(),
    };
    let mut mh: libc::msghdr = unsafe { std::mem::zeroed() };
    mh.msg_iov = &mut iov;
    mh.msg_iovlen = 1;
    mh.msg_control = cmsg.as_mut_ptr().cast();
    mh.msg_controllen = cmsg.len() as _;
    let r = unsafe { libc::recvmsg(fd, &mut mh, libc::MSG_CMSG_CLOEXEC) };
    if r < 0 {
        return Err(io::Error::last_os_error());
    }
    unsafe {
        let mut c = libc::CMSG_FIRSTHDR(&mh);
        while !c.is_null() {
            if (*c).cmsg_level == libc::SOL_SOCKET && (*c).cmsg_type == libc::SCM_RIGHTS {
                let data = libc::CMSG_DATA(c) as *const RawFd;
                let cmsg_len = (*c).cmsg_len as usize;
                let header_len = libc::CMSG_LEN(0) as usize;
                if cmsg_len >= header_len {
                    let count = (cmsg_len - header_len) / std::mem::size_of::<RawFd>();
                    for i in 0..count {
                        let raw = *data.add(i);
                        fds.push(OwnedFd::from_raw_fd(raw));
                    }
                }
            }
            c = libc::CMSG_NXTHDR(&mh, c);
        }
    }
    Ok(r as usize)
}

/// Sends bytes and optional file descriptors over a UNIX domain stream socket using SCM_RIGHTS.
/// File descriptors are passed via SCM_RIGHTS on the first chunk sent.
pub fn send_fds(fd: RawFd, data: &[u8], fds: &[OwnedFd]) -> io::Result<()> {
    let raw_fds: Vec<RawFd> = fds.iter().map(|f| f.as_raw_fd()).collect();
    let mut off = 0;
    let mut first = true;
    while off < data.len() {
        let mut iov = libc::iovec {
            iov_base: data[off..].as_ptr() as *mut _,
            iov_len: data.len() - off,
        };
        let mut mh: libc::msghdr = unsafe { std::mem::zeroed() };
        mh.msg_iov = &mut iov;
        mh.msg_iovlen = 1;
        let mut cmsg = Vec::new();
        if first && !raw_fds.is_empty() {
            let space = unsafe {
                libc::CMSG_SPACE((raw_fds.len() * std::mem::size_of::<RawFd>()) as u32) as usize
            };
            cmsg.resize(space.max(1), 0);
            mh.msg_control = cmsg.as_mut_ptr().cast();
            mh.msg_controllen = space as _;
            unsafe {
                let c = libc::CMSG_FIRSTHDR(&mh);
                (*c).cmsg_level = libc::SOL_SOCKET;
                (*c).cmsg_type = libc::SCM_RIGHTS;
                (*c).cmsg_len = libc::CMSG_LEN(
                    (raw_fds.len() * std::mem::size_of::<RawFd>()) as u32,
                ) as _;
                std::ptr::copy_nonoverlapping(
                    raw_fds.as_ptr(),
                    libc::CMSG_DATA(c) as *mut RawFd,
                    raw_fds.len(),
                );
            }
        }
        let r = unsafe { libc::sendmsg(fd, &mh, libc::MSG_NOSIGNAL) };
        if r < 0 {
            let e = io::Error::last_os_error();
            if e.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(e);
        }
        first = false;
        off += r as usize;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;

    #[test]
    fn test_send_and_recv_fds() {
        let (s1, s2) = UnixStream::pair().expect("UnixStream pair failed");
        let (pr, mut pw) = std::io::pipe().expect("pipe failed");
        pw.write_all(b"hello from pipe").expect("pipe write failed");
        drop(pw);

        let sent_fds = vec![OwnedFd::from(pr)];
        send_fds(s1.as_raw_fd(), b"test payload", &sent_fds).expect("send_fds failed");

        let mut buf = [0u8; 64];
        let mut recvd_fds = Vec::new();
        let n = recv_fds(s2.as_raw_fd(), &mut buf, &mut recvd_fds).expect("recv_fds failed");
        assert_eq!(&buf[..n], b"test payload");
        assert_eq!(recvd_fds.len(), 1);

        let mut read_back = String::new();
        let mut file = std::fs::File::from(recvd_fds.pop().unwrap());
        file.read_to_string(&mut read_back).expect("read pipe back");
        assert_eq!(read_back, "hello from pipe");
    }
}
