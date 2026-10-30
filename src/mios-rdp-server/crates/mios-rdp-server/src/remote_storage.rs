// AI-hint: Native session-owned FUSE mounts backed by RDPDR client filesystem requests. Serial I/O and timeout poisoning prevent late completions from being reused.
// AI-related: redirect.rs, config.rs, pipeline.rs, usr/share/mios/mios.toml
// AI-functions: Factory, Backend, DriveFs, Transport
#![forbid(unsafe_code)]

use crate::{config::Config, pipeline::PipeStats};
use fuser::{
    Errno, FileAttr, FileHandle, FileType, Filesystem, FopenFlags, Generation, INodeNo, Request,
};
use ironrdp_core::impl_as_any;
use ironrdp_pdu::PduResult;
use ironrdp_rdpdr::pdu::efs::*;
use ironrdp_server::{
    RdpdrServerBackend, RdpdrServerFactory, RdpdrServerMessage as Message, ServerEvent,
    ServerEventSender,
};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::ffi::OsStr;
use std::path::PathBuf;
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tracing::{info, warn};
type EventSender = tokio::sync::mpsc::UnboundedSender<ServerEvent>;
type Result<T> = std::result::Result<T, Errno>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Kind {
    Create,
    Close,
    Read,
    Write,
    Query,
    Set,
    Directory,
    Flush,
    Volume,
}
#[derive(Debug)]
enum Response {
    Create(NtStatus, u32),
    Close(NtStatus),
    Read(NtStatus, Vec<u8>),
    Write(NtStatus, u32),
    Query(NtStatus, Option<FileInformationClass>),
    Set(NtStatus),
    Directory(NtStatus, Option<FileInformationClass>),
    Flush(NtStatus),
    Volume(NtStatus, Option<FileSystemInformationClass>),
}
impl Response {
    fn kind(&self) -> Kind {
        match self {
            Self::Create(..) => Kind::Create,
            Self::Close(..) => Kind::Close,
            Self::Read(..) => Kind::Read,
            Self::Write(..) => Kind::Write,
            Self::Query(..) => Kind::Query,
            Self::Set(..) => Kind::Set,
            Self::Directory(..) => Kind::Directory,
            Self::Flush(..) => Kind::Flush,
            Self::Volume(..) => Kind::Volume,
        }
    }
    fn status(&self) -> NtStatus {
        match self {
            Self::Create(s, _)
            | Self::Close(s)
            | Self::Read(s, _)
            | Self::Write(s, _)
            | Self::Query(s, _)
            | Self::Set(s)
            | Self::Directory(s, _)
            | Self::Flush(s)
            | Self::Volume(s, _) => *s,
        }
    }
}
fn status(s: NtStatus) -> Result<()> {
    if s == NtStatus::SUCCESS {
        return Ok(());
    }
    Err(match s {
        NtStatus::NO_SUCH_FILE | NtStatus::OBJECT_PATH_NOT_FOUND => Errno::ENOENT,
        NtStatus::ACCESS_DENIED | NtStatus::PRIVILEGE_NOT_HELD => Errno::EACCES,
        NtStatus::OBJECT_NAME_COLLISION => Errno::EEXIST,
        NtStatus::NOT_A_DIRECTORY => Errno::ENOTDIR,
        NtStatus::FILE_IS_A_DIRECTORY => Errno::EISDIR,
        NtStatus::DIRECTORY_NOT_EMPTY => Errno::ENOTEMPTY,
        NtStatus::DISK_FULL => Errno::ENOSPC,
        NtStatus::MEDIA_WRITE_PROTECTED => Errno::EROFS,
        NtStatus::INVALID_HANDLE => Errno::EBADF,
        NtStatus::INVALID_PARAMETER | NtStatus::OBJECT_NAME_INVALID => Errno::EINVAL,
        NtStatus::NOT_SUPPORTED | NtStatus::NOT_IMPLEMENTED => Errno::EOPNOTSUPP,
        NtStatus::SHARING_VIOLATION | NtStatus::LOCK_NOT_GRANTED => Errno::EBUSY,
        _ => Errno::EIO,
    })
}
struct Pending {
    device: u32,
    kind: Kind,
    reply: mpsc::SyncSender<Result<Response>>,
}
struct Transport {
    sender: EventSender,
    serial: Mutex<()>,
    pending: Mutex<Option<Pending>>,
    removed: Mutex<HashSet<u32>>,
    closed: AtomicBool,
    timeout: Duration,
    stats: Arc<PipeStats>,
}
impl Transport {
    fn call(&self, device: u32, kind: Kind, message: Message) -> Result<Response> {
        let _serial = self.serial.lock().unwrap();
        if self.closed.load(Ordering::Acquire) || self.removed.lock().unwrap().contains(&device) {
            return Err(Errno::ENODEV);
        }
        let (tx, rx) = mpsc::sync_channel(1);
        *self.pending.lock().unwrap() = Some(Pending {
            device,
            kind,
            reply: tx,
        });
        if self.sender.send(ServerEvent::Rdpdr(message)).is_err() {
            self.close();
            return Err(Errno::ENOTCONN);
        }
        match rx.recv_timeout(self.timeout) {
            Ok(reply) => reply,
            Err(_) => {
                // IronRDP owns CompletionId allocation. Stop all subsequent calls
                // after a timeout so no late response can answer a newer request.
                self.close();
                *self.stats.drive_error.lock().unwrap() =
                    Some("remote file request timed out; reconnect the session".into());
                Err(Errno::ETIMEDOUT)
            }
        }
    }
    fn complete(&self, device: u32, response: Response) {
        let mut pending = self.pending.lock().unwrap();
        if pending.as_ref().is_some_and(|p| p.device == device) {
            let request = pending.take().unwrap();
            let reply = if request.kind == response.kind() {
                Ok(response)
            } else {
                self.closed.store(true, Ordering::Release);
                Err(Errno::EPROTO)
            };
            let _ = request.reply.send(reply);
        }
    }
    fn remove(&self, device: u32) {
        self.removed.lock().unwrap().insert(device);
        let mut pending = self.pending.lock().unwrap();
        if pending.as_ref().is_some_and(|p| p.device == device) {
            let _ = pending.take().unwrap().reply.send(Err(Errno::ENODEV));
        }
    }
    fn close(&self) {
        self.closed.store(true, Ordering::Release);
        if let Some(pending) = self.pending.lock().unwrap().take() {
            let _ = pending.reply.send(Err(Errno::ENOTCONN));
        }
    }
}
fn component(name: &OsStr) -> Result<&str> {
    let name = name.to_str().ok_or(Errno::EILSEQ)?;
    if name.is_empty()
        || name == "."
        || name == ".."
        || name.len() > 255
        || name.chars().any(|c| {
            c.is_control() || matches!(c, '/' | '\\' | ':' | '*' | '?' | '"' | '<' | '>' | '|')
        })
    {
        return Err(Errno::EINVAL);
    }
    Ok(name)
}
fn child(parent: &str, name: &OsStr) -> Result<String> {
    let name = component(name)?;
    let path = format!("{}\\{name}", parent.trim_end_matches('\\'));
    if path.len() > 32760 {
        return Err(Errno::ENAMETOOLONG);
    }
    Ok(path)
}
fn nt_time(value: i64) -> SystemTime {
    let Some(ticks) = value
        .checked_sub(116_444_736_000_000_000)
        .filter(|v| *v >= 0)
    else {
        return UNIX_EPOCH;
    };
    UNIX_EPOCH
        .checked_add(Duration::from_nanos((ticks as u64).saturating_mul(100)))
        .unwrap_or(UNIX_EPOCH)
}
struct Nodes {
    paths: BTreeMap<u64, String>,
    reverse: HashMap<String, u64>,
    references: HashMap<u64, (u64, u64)>, // kernel lookups, open file/directory handles
    next: u64,
}
impl Nodes {
    fn new() -> Self {
        Self {
            paths: BTreeMap::from([(1, "\\".into())]),
            reverse: HashMap::from([("\\".into(), 1)]),
            references: HashMap::from([(1, (1, 0))]),
            next: 2,
        }
    }
    fn insert(&mut self, path: String, limit: usize) -> Result<INodeNo> {
        if let Some(ino) = self.reverse.get(&path) {
            return Ok(INodeNo(*ino));
        }
        if self.references.len() >= limit {
            let unused = self
                .paths
                .keys()
                .copied()
                .find(|ino| *ino != 1 && self.references.get(ino) == Some(&(0, 0)));
            if let Some(ino) = unused {
                self.discard_unused(INodeNo(ino));
            } else {
                return Err(Errno::ENOSPC);
            }
        }
        let ino = self.next;
        self.next = self.next.checked_add(1).ok_or(Errno::EOVERFLOW)?;
        self.paths.insert(ino, path.clone());
        self.reverse.insert(path, ino);
        self.references.insert(ino, (0, 0));
        Ok(INodeNo(ino))
    }
    fn lookup(&mut self, ino: INodeNo) {
        let entry = self.references.entry(ino.0).or_default();
        entry.0 = entry.0.saturating_add(1);
    }
    fn open(&mut self, ino: INodeNo) {
        let entry = self.references.entry(ino.0).or_default();
        entry.1 = entry.1.saturating_add(1);
    }
    fn forget(&mut self, ino: INodeNo, count: u64) {
        if let Some(entry) = self.references.get_mut(&ino.0) {
            entry.0 = entry.0.saturating_sub(count);
        }
        self.discard_unused(ino);
    }
    fn release(&mut self, ino: INodeNo) {
        if let Some(entry) = self.references.get_mut(&ino.0) {
            entry.1 = entry.1.saturating_sub(1);
        }
        self.discard_unused(ino);
    }
    fn discard_unused(&mut self, ino: INodeNo) {
        if ino.0 != 1 && self.references.get(&ino.0) == Some(&(0, 0)) {
            self.references.remove(&ino.0);
            if let Some(path) = self.paths.remove(&ino.0) {
                if self.reverse.get(&path) == Some(&ino.0) {
                    self.reverse.remove(&path);
                }
            }
        }
    }
    fn invalidate_path(&mut self, path: &str) {
        if let Some(ino) = self.reverse.remove(path) {
            self.paths.remove(&ino);
            self.discard_unused(INodeNo(ino));
        }
    }
}
struct DriveFs {
    io: Arc<Transport>,
    device: u32,
    uid: u32,
    gid: u32,
    max_entries: usize,
    max_io: u32,
    nodes: Mutex<Nodes>,
    directories: Mutex<HashMap<u64, Vec<(INodeNo, FileType, String)>>>,
}
impl DriveFs {
    fn path(&self, ino: INodeNo) -> Result<String> {
        self.nodes
            .lock()
            .unwrap()
            .paths
            .get(&ino.0)
            .cloned()
            .ok_or(Errno::ENOENT)
    }
    fn request(&self, kind: Kind, message: Message) -> Result<Response> {
        self.io.call(self.device, kind, message)
    }
    fn open_path(
        &self,
        path: String,
        desired_access: DesiredAccess,
        create_disposition: CreateDisposition,
        create_options: CreateOptions,
    ) -> Result<u32> {
        match self.request(
            Kind::Create,
            Message::Create {
                device_id: self.device,
                path,
                desired_access,
                create_disposition,
                create_options,
            },
        )? {
            Response::Create(s, id) => {
                status(s)?;
                Ok(id)
            }
            _ => Err(Errno::EPROTO),
        }
    }
    fn close_file(&self, file_id: u32) -> Result<()> {
        status(
            self.request(
                Kind::Close,
                Message::Close {
                    device_id: self.device,
                    file_id,
                },
            )?
            .status(),
        )
    }
    fn query(
        &self,
        file_id: u32,
        info_class: FileInformationClassLevel,
    ) -> Result<FileInformationClass> {
        match self.request(
            Kind::Query,
            Message::QueryInformation {
                device_id: self.device,
                file_id,
                info_class,
            },
        )? {
            Response::Query(s, buffer) => {
                status(s)?;
                buffer.ok_or(Errno::EIO)
            }
            _ => Err(Errno::EPROTO),
        }
    }
    fn set(&self, file_id: u32, set_buffer: FileInformationClass) -> Result<()> {
        status(
            self.request(
                Kind::Set,
                Message::SetInformation {
                    device_id: self.device,
                    file_id,
                    set_buffer,
                },
            )?
            .status(),
        )
    }
    fn attr(&self, ino: INodeNo, handle: Option<u32>) -> Result<FileAttr> {
        if ino.0 == 1 && handle.is_none() {
            return Ok(self.make_attr(
                ino,
                0,
                FileType::Directory,
                UNIX_EPOCH,
                UNIX_EPOCH,
                UNIX_EPOCH,
                UNIX_EPOCH,
                false,
            ));
        }
        let owned = handle.is_none();
        let file = match handle {
            Some(file) => file,
            None => self.open_path(
                self.path(ino)?,
                DesiredAccess::FILE_READ_ATTRIBUTES,
                CreateDisposition::FILE_OPEN,
                CreateOptions::FILE_OPEN_REPARSE_POINT,
            )?,
        };
        let result = (|| {
            let FileInformationClass::Basic(basic) =
                self.query(file, FileInformationClassLevel::FILE_BASIC_INFORMATION)?
            else {
                return Err(Errno::EPROTO);
            };
            if basic
                .file_attributes
                .contains(FileAttributes::FILE_ATTRIBUTE_REPARSE_POINT)
            {
                return Err(Errno::ELOOP);
            }
            let FileInformationClass::Standard(standard) =
                self.query(file, FileInformationClassLevel::FILE_STANDARD_INFORMATION)?
            else {
                return Err(Errno::EPROTO);
            };
            if standard.end_of_file < 0 {
                return Err(Errno::EIO);
            }
            let directory = standard.directory == Boolean::True;
            Ok(self.make_attr(
                ino,
                standard.end_of_file as u64,
                if directory {
                    FileType::Directory
                } else {
                    FileType::RegularFile
                },
                nt_time(basic.last_access_time),
                nt_time(basic.last_write_time),
                nt_time(basic.change_time),
                nt_time(basic.creation_time),
                !directory
                    && basic
                        .file_attributes
                        .contains(FileAttributes::FILE_ATTRIBUTE_READONLY),
            ))
        })();
        if owned {
            let _ = self.close_file(file);
        }
        result
    }
    #[allow(clippy::too_many_arguments)]
    fn make_attr(
        &self,
        ino: INodeNo,
        size: u64,
        kind: FileType,
        atime: SystemTime,
        mtime: SystemTime,
        ctime: SystemTime,
        crtime: SystemTime,
        readonly: bool,
    ) -> FileAttr {
        FileAttr {
            ino,
            size,
            blocks: size.div_ceil(512),
            atime,
            mtime,
            ctime,
            crtime,
            kind,
            perm: if kind == FileType::Directory {
                0o700
            } else if readonly {
                0o400
            } else {
                0o600
            },
            nlink: 1,
            uid: self.uid,
            gid: self.gid,
            rdev: 0,
            blksize: 4096,
            flags: 0,
        }
    }
    fn list(&self, ino: INodeNo, file_id: u32) -> Result<Vec<(INodeNo, FileType, String)>> {
        let parent = self.path(ino)?;
        let mut entries = vec![
            (ino, FileType::Directory, ".".into()),
            (INodeNo(1), FileType::Directory, "..".into()),
        ];
        let mut initial = true;
        let mut queried = 0;
        loop {
            queried += 1;
            if queried > self.max_entries {
                return Err(Errno::EOVERFLOW);
            }
            let response = self.request(
                Kind::Directory,
                Message::QueryDirectory {
                    device_id: self.device,
                    file_id,
                    info_class: FileInformationClassLevel::FILE_DIRECTORY_INFORMATION,
                    path: if initial {
                        format!("{}\\*", parent.trim_end_matches('\\'))
                    } else {
                        String::new()
                    },
                    initial_query: initial,
                },
            )?;
            initial = false;
            let Response::Directory(s, buffer) = response else {
                return Err(Errno::EPROTO);
            };
            if s == NtStatus::NO_MORE_FILES {
                break;
            }
            status(s)?;
            let Some(FileInformationClass::Directory(entry)) = buffer else {
                return Err(Errno::EPROTO);
            };
            if entry.file_name == "." || entry.file_name == ".." {
                continue;
            }
            if entries.len() >= self.max_entries {
                return Err(Errno::EOVERFLOW);
            }
            let path = child(&parent, OsStr::new(&entry.file_name))?;
            if entry
                .file_attributes
                .contains(FileAttributes::FILE_ATTRIBUTE_REPARSE_POINT)
            {
                continue;
            }
            let child_ino = self.nodes.lock().unwrap().insert(path, self.max_entries)?;
            entries.push((
                child_ino,
                if entry
                    .file_attributes
                    .contains(FileAttributes::FILE_ATTRIBUTE_DIRECTORY)
                {
                    FileType::Directory
                } else {
                    FileType::RegularFile
                },
                entry.file_name,
            ));
        }
        Ok(entries)
    }
    fn remove_path(&self, parent: INodeNo, name: &OsStr, directory: bool) -> Result<()> {
        let path = child(&self.path(parent)?, name)?;
        let file = self.open_path(
            path.clone(),
            DesiredAccess::DELETE,
            CreateDisposition::FILE_OPEN,
            if directory {
                CreateOptions::FILE_DIRECTORY_FILE
            } else {
                CreateOptions::FILE_NON_DIRECTORY_FILE
            },
        )?;
        let result = self.set(
            file,
            FileInformationClass::Disposition(FileDispositionInformation { delete_pending: 1 }),
        );
        let close = self.close_file(file);
        result.and(close)?;
        self.nodes.lock().unwrap().invalidate_path(&path);
        Ok(())
    }
}
const TTL: Duration = Duration::from_secs(1);

impl Filesystem for DriveFs {
    fn init(&mut self, _req: &Request, config: &mut fuser::KernelConfig) -> std::io::Result<()> {
        config
            .set_max_write(self.max_io)
            .map_err(|_| std::io::Error::other("invalid native drive IO limit"))?;
        Ok(())
    }
    fn lookup(&self, _req: &Request, parent: INodeNo, name: &OsStr, reply: fuser::ReplyEntry) {
        let mut candidate = None;
        let result = (|| {
            let path = child(&self.path(parent)?, name)?;
            let ino = self.nodes.lock().unwrap().insert(path, self.max_entries)?;
            candidate = Some(ino);
            self.attr(ino, None)
        })();
        match result {
            Ok(attr) => {
                self.nodes.lock().unwrap().lookup(attr.ino);
                reply.entry(&TTL, &attr, Generation(0));
            }
            Err(e) => {
                if let Some(ino) = candidate {
                    self.nodes.lock().unwrap().discard_unused(ino);
                }
                reply.error(e);
            }
        }
    }
    fn forget(&self, _req: &Request, ino: INodeNo, nlookup: u64) {
        self.nodes.lock().unwrap().forget(ino, nlookup);
    }
    fn getattr(
        &self,
        _req: &Request,
        ino: INodeNo,
        fh: Option<FileHandle>,
        reply: fuser::ReplyAttr,
    ) {
        match self.attr(ino, fh.map(|f| f.0 as u32)) {
            Ok(attr) => reply.attr(&TTL, &attr),
            Err(e) => reply.error(e),
        }
    }
    fn open(&self, _req: &Request, ino: INodeNo, flags: fuser::OpenFlags, reply: fuser::ReplyOpen) {
        let result = (|| {
            let bits = flags.0;
            let mode = bits & libc::O_ACCMODE;
            self.open_path(
                self.path(ino)?,
                DesiredAccess::FILE_READ_ATTRIBUTES
                    | match mode {
                        libc::O_WRONLY => DesiredAccess::GENERIC_WRITE,
                        libc::O_RDWR => DesiredAccess::GENERIC_READ | DesiredAccess::GENERIC_WRITE,
                        _ => DesiredAccess::GENERIC_READ,
                    },
                if bits & libc::O_TRUNC != 0 {
                    CreateDisposition::FILE_OVERWRITE
                } else {
                    CreateDisposition::FILE_OPEN
                },
                CreateOptions::FILE_NON_DIRECTORY_FILE,
            )
        })();
        match result {
            Ok(id) => {
                self.nodes.lock().unwrap().open(ino);
                reply.opened(FileHandle(u64::from(id)), FopenFlags::FOPEN_DIRECT_IO);
            }
            Err(e) => reply.error(e),
        }
    }
    fn create(
        &self,
        _req: &Request,
        parent: INodeNo,
        name: &OsStr,
        _mode: u32,
        _umask: u32,
        flags: i32,
        reply: fuser::ReplyCreate,
    ) {
        let result = (|| {
            let path = child(&self.path(parent)?, name)?;
            let disposition = if flags & libc::O_EXCL != 0 {
                CreateDisposition::FILE_CREATE
            } else if flags & libc::O_TRUNC != 0 {
                CreateDisposition::FILE_OVERWRITE_IF
            } else {
                CreateDisposition::FILE_OPEN_IF
            };
            let file = self.open_path(
                path.clone(),
                DesiredAccess::GENERIC_READ | DesiredAccess::GENERIC_WRITE,
                disposition,
                CreateOptions::FILE_NON_DIRECTORY_FILE,
            )?;
            let attr = (|| {
                let ino = self.nodes.lock().unwrap().insert(path, self.max_entries)?;
                self.attr(ino, Some(file))
            })();
            match attr {
                Ok(attr) => Ok((attr, file)),
                Err(e) => {
                    let _ = self.close_file(file);
                    Err(e)
                }
            }
        })();
        match result {
            Ok((attr, file)) => {
                let mut nodes = self.nodes.lock().unwrap();
                nodes.lookup(attr.ino);
                nodes.open(attr.ino);
                drop(nodes);
                reply.created(
                    &TTL,
                    &attr,
                    Generation(0),
                    FileHandle(u64::from(file)),
                    FopenFlags::FOPEN_DIRECT_IO,
                );
            }
            Err(e) => reply.error(e),
        }
    }
    fn read(
        &self,
        _req: &Request,
        _ino: INodeNo,
        fh: FileHandle,
        offset: u64,
        size: u32,
        _flags: fuser::OpenFlags,
        _lock: Option<fuser::LockOwner>,
        reply: fuser::ReplyData,
    ) {
        let length = size.min(self.max_io);
        let result = self.request(
            Kind::Read,
            Message::Read {
                device_id: self.device,
                file_id: fh.0 as u32,
                length,
                offset,
            },
        );
        match result {
            Ok(Response::Read(s, _)) if s == NtStatus::END_OF_FILE => reply.data(&[]),
            Ok(Response::Read(s, data)) => match status(s) {
                Ok(()) if data.len() <= length as usize => reply.data(&data),
                Ok(()) => reply.error(Errno::EPROTO),
                Err(e) => reply.error(e),
            },
            Ok(_) => reply.error(Errno::EPROTO),
            Err(e) => reply.error(e),
        }
    }
    fn write(
        &self,
        _req: &Request,
        _ino: INodeNo,
        fh: FileHandle,
        offset: u64,
        data: &[u8],
        _write_flags: fuser::WriteFlags,
        _flags: fuser::OpenFlags,
        _lock: Option<fuser::LockOwner>,
        reply: fuser::ReplyWrite,
    ) {
        let length = data.len().min(self.max_io as usize);
        let result = self.request(
            Kind::Write,
            Message::Write {
                device_id: self.device,
                file_id: fh.0 as u32,
                data: data[..length].to_vec(),
                offset,
            },
        );
        match result {
            Ok(Response::Write(s, n)) => match status(s) {
                Ok(()) if n as usize <= length => reply.written(n),
                Ok(()) => reply.error(Errno::EPROTO),
                Err(e) => reply.error(e),
            },
            Ok(_) => reply.error(Errno::EPROTO),
            Err(e) => reply.error(e),
        }
    }
    fn release(
        &self,
        _req: &Request,
        ino: INodeNo,
        fh: FileHandle,
        _flags: fuser::OpenFlags,
        _lock: Option<fuser::LockOwner>,
        _flush: bool,
        reply: fuser::ReplyEmpty,
    ) {
        self.nodes.lock().unwrap().release(ino);
        match self.close_file(fh.0 as u32) {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(e),
        }
    }
    fn fsync(
        &self,
        _req: &Request,
        _ino: INodeNo,
        fh: FileHandle,
        _data: bool,
        reply: fuser::ReplyEmpty,
    ) {
        match self
            .request(
                Kind::Flush,
                Message::FlushBuffers {
                    device_id: self.device,
                    file_id: fh.0 as u32,
                },
            )
            .and_then(|r| status(r.status()))
        {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(e),
        }
    }
    fn statfs(&self, _req: &Request, _ino: INodeNo, reply: fuser::ReplyStatfs) {
        let result = (|| {
            let file = self.open_path(
                "\\".into(),
                DesiredAccess::GENERIC_READ,
                CreateDisposition::FILE_OPEN,
                CreateOptions::FILE_DIRECTORY_FILE,
            )?;
            let response = self.request(
                Kind::Volume,
                Message::QueryVolumeInformation {
                    device_id: self.device,
                    file_id: file,
                    fs_info_class: FileSystemInformationClassLevel::FILE_FS_SIZE_INFORMATION,
                },
            );
            let close = self.close_file(file);
            let Response::Volume(s, buffer) = response? else {
                return Err(Errno::EPROTO);
            };
            status(s)?;
            close?;
            let Some(FileSystemInformationClass::FileFsSizeInformation(size)) = buffer else {
                return Err(Errno::EPROTO);
            };
            if size.total_alloc_units < 0
                || size.available_alloc_units < 0
                || size.available_alloc_units > size.total_alloc_units
            {
                return Err(Errno::EIO);
            }
            let block = size
                .bytes_per_sector
                .checked_mul(size.sectors_per_alloc_unit)
                .filter(|s| *s > 0)
                .ok_or(Errno::EOVERFLOW)?;
            Ok((
                size.total_alloc_units as u64,
                size.available_alloc_units as u64,
                block,
            ))
        })();
        match result {
            Ok((blocks, available, block)) => {
                reply.statfs(blocks, available, available, 0, 0, block, 255, block)
            }
            Err(e) => reply.error(e),
        }
    }
    fn opendir(
        &self,
        _req: &Request,
        ino: INodeNo,
        _flags: fuser::OpenFlags,
        reply: fuser::ReplyOpen,
    ) {
        let result = self.path(ino).and_then(|path| {
            self.open_path(
                path,
                DesiredAccess::GENERIC_READ,
                CreateDisposition::FILE_OPEN,
                CreateOptions::FILE_DIRECTORY_FILE,
            )
        });
        match result {
            Ok(id) => {
                self.nodes.lock().unwrap().open(ino);
                reply.opened(FileHandle(u64::from(id)), FopenFlags::empty());
            }
            Err(e) => reply.error(e),
        }
    }
    fn readdir(
        &self,
        _req: &Request,
        ino: INodeNo,
        fh: FileHandle,
        offset: u64,
        mut reply: fuser::ReplyDirectory,
    ) {
        if offset == 0 {
            match self.list(ino, fh.0 as u32) {
                Ok(entries) => {
                    self.directories.lock().unwrap().insert(fh.0, entries);
                }
                Err(e) => {
                    reply.error(e);
                    return;
                }
            }
        }
        let dirs = self.directories.lock().unwrap();
        let Some(entries) = dirs.get(&fh.0) else {
            reply.error(Errno::EINVAL);
            return;
        };
        for (index, (ino, kind, name)) in entries.iter().enumerate().skip(offset as usize) {
            if reply.add(*ino, (index + 1) as u64, *kind, name) {
                break;
            }
        }
        reply.ok();
    }
    fn releasedir(
        &self,
        _req: &Request,
        ino: INodeNo,
        fh: FileHandle,
        _flags: fuser::OpenFlags,
        reply: fuser::ReplyEmpty,
    ) {
        self.directories.lock().unwrap().remove(&fh.0);
        self.nodes.lock().unwrap().release(ino);
        match self.close_file(fh.0 as u32) {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(e),
        }
    }
    fn mkdir(
        &self,
        _req: &Request,
        parent: INodeNo,
        name: &OsStr,
        _mode: u32,
        _umask: u32,
        reply: fuser::ReplyEntry,
    ) {
        let result = (|| {
            let path = child(&self.path(parent)?, name)?;
            let file = self.open_path(
                path.clone(),
                DesiredAccess::GENERIC_READ | DesiredAccess::GENERIC_WRITE,
                CreateDisposition::FILE_CREATE,
                CreateOptions::FILE_DIRECTORY_FILE,
            )?;
            let result = (|| {
                let ino = self.nodes.lock().unwrap().insert(path, self.max_entries)?;
                self.attr(ino, Some(file))
            })();
            let _ = self.close_file(file);
            result
        })();
        match result {
            Ok(attr) => {
                self.nodes.lock().unwrap().lookup(attr.ino);
                reply.entry(&TTL, &attr, Generation(0));
            }
            Err(e) => reply.error(e),
        }
    }
    fn unlink(&self, _req: &Request, parent: INodeNo, name: &OsStr, reply: fuser::ReplyEmpty) {
        match self.remove_path(parent, name, false) {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(e),
        }
    }
    fn rmdir(&self, _req: &Request, parent: INodeNo, name: &OsStr, reply: fuser::ReplyEmpty) {
        match self.remove_path(parent, name, true) {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(e),
        }
    }
    fn rename(
        &self,
        _req: &Request,
        parent: INodeNo,
        name: &OsStr,
        newparent: INodeNo,
        newname: &OsStr,
        flags: fuser::RenameFlags,
        reply: fuser::ReplyEmpty,
    ) {
        let result = (|| {
            if flags.bits() & !(libc::RENAME_NOREPLACE as u32) != 0 {
                return Err(Errno::EINVAL);
            }
            let old = child(&self.path(parent)?, name)?;
            let new = child(&self.path(newparent)?, newname)?;
            let file = self.open_path(
                old.clone(),
                DesiredAccess::DELETE,
                CreateDisposition::FILE_OPEN,
                CreateOptions::empty(),
            )?;
            let result = self.set(
                file,
                FileInformationClass::Rename(FileRenameInformation {
                    replace_if_exists: u8::from(flags.bits() == 0).into(),
                    file_name: new.clone(),
                }),
            );
            let close = self.close_file(file);
            result.and(close)?;
            let mut nodes = self.nodes.lock().unwrap();
            nodes.invalidate_path(&new);
            let updates: Vec<_> = nodes
                .paths
                .iter()
                .filter_map(|(ino, path)| {
                    if path == &old || path.starts_with(&(old.clone() + "\\")) {
                        Some((*ino, path.clone(), format!("{}{}", new, &path[old.len()..])))
                    } else {
                        None
                    }
                })
                .collect();
            for (ino, old, new) in updates {
                nodes.reverse.remove(&old);
                nodes.reverse.insert(new.clone(), ino);
                nodes.paths.insert(ino, new);
            }
            Ok(())
        })();
        match result {
            Ok(()) => reply.ok(),
            Err(e) => reply.error(e),
        }
    }
    fn setattr(
        &self,
        _req: &Request,
        ino: INodeNo,
        mode: Option<u32>,
        uid: Option<u32>,
        gid: Option<u32>,
        size: Option<u64>,
        _atime: Option<fuser::TimeOrNow>,
        _mtime: Option<fuser::TimeOrNow>,
        _ctime: Option<SystemTime>,
        fh: Option<FileHandle>,
        _crtime: Option<SystemTime>,
        _chgtime: Option<SystemTime>,
        _bkuptime: Option<SystemTime>,
        _flags: Option<fuser::BsdFileFlags>,
        reply: fuser::ReplyAttr,
    ) {
        let result = (|| {
            if uid.is_some_and(|u| u != self.uid)
                || gid.is_some_and(|g| g != self.gid)
                || mode.is_some_and(|m| m & 0o7777 != 0o600)
            {
                return Err(Errno::EPERM);
            }
            if let Some(size) = size {
                let size = i64::try_from(size).map_err(|_| Errno::EFBIG)?;
                let owned = fh.is_none();
                let file = match fh {
                    Some(file) => file.0 as u32,
                    None => self.open_path(
                        self.path(ino)?,
                        DesiredAccess::GENERIC_WRITE,
                        CreateDisposition::FILE_OPEN,
                        CreateOptions::FILE_NON_DIRECTORY_FILE,
                    )?,
                };
                let result = self.set(
                    file,
                    FileInformationClass::EndOfFile(FileEndOfFileInformation { end_of_file: size }),
                );
                if owned {
                    let _ = self.close_file(file);
                }
                result?;
            }
            self.attr(ino, fh.map(|f| f.0 as u32))
        })();
        match result {
            Ok(attr) => reply.attr(&TTL, &attr),
            Err(e) => reply.error(e),
        }
    }
}

struct Mount {
    path: PathBuf,
    session: fuser::BackgroundSession,
}
pub struct Backend {
    cfg: Config,
    stats: Arc<PipeStats>,
    io: Option<Arc<Transport>>,
    mounts: HashMap<u32, Mount>,
    root: PathBuf,
}
impl std::fmt::Debug for Backend {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("RemoteStorage")
            .field("mounts", &self.mounts.len())
            .finish()
    }
}
impl_as_any!(Backend);
impl Backend {
    fn new(cfg: Config, stats: Arc<PipeStats>, sender: Option<EventSender>) -> Self {
        let generation = stats.drive_generation.fetch_add(1, Ordering::AcqRel) + 1;
        let root = cfg
            .runtime_dir
            .join("mios-rdp/drives")
            .join(format!("session-{}-{generation}", std::process::id()));
        let io = sender.map(|sender| {
            Arc::new(Transport {
                sender,
                serial: Mutex::new(()),
                pending: Mutex::new(None),
                removed: Mutex::new(HashSet::new()),
                closed: AtomicBool::new(false),
                timeout: Duration::from_millis(cfg.native.drive_timeout_ms),
                stats: Arc::clone(&stats),
            })
        });
        Self {
            cfg,
            stats,
            io,
            mounts: HashMap::new(),
            root,
        }
    }
    fn cleanup(&mut self, id: u32) {
        if let Some(mount) = self.mounts.remove(&id) {
            if let Some(io) = &self.io {
                io.remove(id);
            }
            let stats = Arc::clone(&self.stats);
            let root = self.root.clone();
            stats.drive_workers.fetch_add(1, Ordering::AcqRel);
            std::thread::spawn(move || {
                if let Err(error) = mount.session.umount_and_join() {
                    warn!(%error,"remote drive unmount failed");
                }
                let _ = std::fs::remove_dir(&mount.path);
                let _ = std::fs::remove_dir(root);
                stats
                    .drive_mounts
                    .lock()
                    .unwrap()
                    .retain(|entry| entry["path"].as_str() != mount.path.to_str());
                stats.drive_workers.fetch_sub(1, Ordering::AcqRel);
            });
        }
    }
    fn finish(&mut self) {
        if let Some(io) = &self.io {
            io.close();
        }
        let ids: Vec<_> = self.mounts.keys().copied().collect();
        for id in ids {
            self.cleanup(id);
        }
    }
    fn complete(&self, device: u32, response: Response) {
        if let Some(io) = &self.io {
            io.complete(device, response);
        }
    }
}
impl Drop for Backend {
    fn drop(&mut self) {
        self.finish();
    }
}
impl RdpdrServerBackend for Backend {
    fn on_device_announce(&mut self, devices: &[DeviceAnnounceHeader]) -> Vec<(u32, bool)> {
        use std::os::unix::fs::{DirBuilderExt, MetadataExt};
        devices.iter().map(|device| {
            let id=device.device_id();
            let result=(||->anyhow::Result<()> {
                if !self.cfg.redirect.drives {anyhow::bail!("client filesystem forwarding is disabled");}
                let io=self.io.as_ref().ok_or_else(||anyhow::anyhow!("RDPDR sender unavailable"))?;
                // The upstream header exposes its codec but not its type getter.
                // Inspect the public wire representation without private-field access.
                let announce=ClientDeviceListAnnounce {device_list:vec![device.clone()]};
                if announce.size()>65536 {anyhow::bail!("device announce too large");}
                let mut header=vec![0;announce.size()];announce.encode(&mut ironrdp_core::WriteCursor::new(&mut header))?;
                if header[4..8]!=u32::from(DeviceType::Filesystem).to_le_bytes() {anyhow::bail!("only filesystem devices are supported");}
                let removed=io.removed.lock().unwrap();
                if self.mounts.contains_key(&id)||removed.contains(&id)||self.mounts.len()+removed.len()>=self.cfg.native.drive_max_devices {anyhow::bail!("duplicate drive or per-session device limit reached");}drop(removed);
                std::fs::DirBuilder::new().recursive(true).mode(0o700).create(&self.root)?;
                let path=self.root.join(format!("drive-{id}"));std::fs::DirBuilder::new().mode(0o700).create(&path)?;
                let gid=std::fs::metadata(&self.cfg.runtime_dir)?.gid();
                let fs=DriveFs {io:Arc::clone(io),device:id,uid:self.cfg.operator_uid,gid,max_entries:self.cfg.native.drive_max_entries,max_io:self.cfg.native.drive_io_kib*1024,nodes:Mutex::new(Nodes::new()),directories:Mutex::new(HashMap::new())};
                let mut options=fuser::Config::default();options.n_threads=Some(1);
                options.mount_options=vec![fuser::MountOption::FSName(format!("rdp-drive-{id}")),fuser::MountOption::Subtype("mios-rdp-drive".into()),fuser::MountOption::DefaultPermissions,fuser::MountOption::NoDev,fuser::MountOption::NoSuid,fuser::MountOption::NoExec];
                let session=match fuser::spawn_mount(fs,&path,&options) {Ok(session)=>session,Err(e)=>{let _=std::fs::remove_dir(&path);let _=std::fs::remove_dir(&self.root);return Err(e.into());}};
                self.stats.drive_mounts.lock().unwrap().push(serde_json::json!({"device_id":id,"name":device.preferred_dos_name().trim_matches('\0'),"path":path}));
                self.mounts.insert(id,Mount {path,session});Ok(())
            })();
            if let Err(error)=&result {*self.stats.drive_error.lock().unwrap()=Some(error.to_string());warn!(id,%error,"remote drive refused");}
            (id,result.is_ok())
        }).collect()
    }
    fn on_device_remove(&mut self, ids: &[u32]) {
        for id in ids {
            self.cleanup(*id);
        }
    }
    fn on_client_name(&mut self, name: &str) {
        info!(name, "remote storage client connected");
    }
    fn on_create_complete(&mut self, r: &DeviceCreateResponse) -> PduResult<()> {
        self.complete(
            r.device_io_reply.device_id,
            Response::Create(r.device_io_reply.io_status, r.file_id),
        );
        Ok(())
    }
    fn on_close_complete(&mut self, r: &DeviceCloseResponse) -> PduResult<()> {
        self.complete(
            r.device_io_response.device_id,
            Response::Close(r.device_io_response.io_status),
        );
        Ok(())
    }
    fn on_read_complete(&mut self, r: &DeviceReadResponse) -> PduResult<()> {
        self.complete(
            r.device_io_reply.device_id,
            Response::Read(r.device_io_reply.io_status, r.read_data.clone()),
        );
        Ok(())
    }
    fn on_write_complete(&mut self, r: &DeviceWriteResponse) -> PduResult<()> {
        self.complete(
            r.device_io_reply.device_id,
            Response::Write(r.device_io_reply.io_status, r.length),
        );
        Ok(())
    }
    fn on_flush_buffers_complete(&mut self, r: &DeviceFlushBuffersResponse) -> PduResult<()> {
        self.complete(
            r.device_io_response.device_id,
            Response::Flush(r.device_io_response.io_status),
        );
        Ok(())
    }
    fn on_query_information_complete(
        &mut self,
        r: &ClientDriveQueryInformationResponse,
    ) -> PduResult<()> {
        self.complete(
            r.device_io_response.device_id,
            Response::Query(r.device_io_response.io_status, r.buffer.clone()),
        );
        Ok(())
    }
    fn on_set_information_complete(
        &mut self,
        r: &ClientDriveSetInformationResponse,
    ) -> PduResult<()> {
        let mut bytes = vec![0; r.size()];
        r.encode(&mut ironrdp_core::WriteCursor::new(&mut bytes))
            .map_err(|e| ironrdp_pdu::pdu_other_err!("remote drive completion encode",source:e))?;
        let header = DeviceIoResponse::decode(&mut ironrdp_core::ReadCursor::new(&bytes))
            .map_err(|e| ironrdp_pdu::pdu_other_err!("remote drive completion decode",source:e))?;
        self.complete(header.device_id, Response::Set(header.io_status));
        Ok(())
    }
    fn on_query_directory_complete(
        &mut self,
        r: &ClientDriveQueryDirectoryResponse,
    ) -> PduResult<()> {
        self.complete(
            r.device_io_reply.device_id,
            Response::Directory(r.device_io_reply.io_status, r.buffer.clone()),
        );
        Ok(())
    }
    fn on_device_control_complete(&mut self, _: &DeviceControlResponse) -> PduResult<()> {
        Ok(())
    }
    fn on_notify_change_directory_complete(
        &mut self,
        _: &ClientDriveNotifyChangeDirectoryResponse,
    ) -> PduResult<()> {
        Ok(())
    }
    fn on_query_volume_information_complete(
        &mut self,
        response: &ClientDriveQueryVolumeInformationResponse,
    ) -> PduResult<()> {
        self.complete(
            response.device_io_reply.device_id,
            Response::Volume(response.device_io_reply.io_status, response.buffer.clone()),
        );
        Ok(())
    }
    fn on_lock_control_complete(&mut self, _: &ClientDriveLockControlResponse) -> PduResult<()> {
        Ok(())
    }
    fn on_query_security_complete(
        &mut self,
        _: &ClientDriveQuerySecurityResponse,
    ) -> PduResult<()> {
        Ok(())
    }
    fn on_set_security_complete(&mut self, _: &ClientDriveSetSecurityResponse) -> PduResult<()> {
        Ok(())
    }
    fn close(&mut self) {
        self.finish();
    }
}
pub struct Factory {
    cfg: Config,
    stats: Arc<PipeStats>,
    sender: Mutex<Option<EventSender>>,
}
impl Factory {
    pub fn new(cfg: Config, stats: Arc<PipeStats>) -> Self {
        Self {
            cfg,
            stats,
            sender: Mutex::new(None),
        }
    }
}
impl ServerEventSender for Factory {
    fn set_sender(&mut self, sender: EventSender) {
        *self.sender.lock().unwrap() = Some(sender);
    }
}
impl RdpdrServerFactory for Factory {
    fn build_backend(&self) -> Box<dyn RdpdrServerBackend> {
        Box::new(Backend::new(
            self.cfg.clone(),
            Arc::clone(&self.stats),
            self.sender.lock().unwrap().clone(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn components_cannot_escape_share() {
        assert_eq!(
            child("\\folder", OsStr::new("résumé.txt")).unwrap(),
            "\\folder\\résumé.txt"
        );
        for bad in ["", ".", "..", "a/b", "a\\b", "C:escape", "a\0b", "*.txt"] {
            assert!(child("\\", OsStr::new(bad)).is_err(), "{bad:?}");
        }
        let mut nodes = Nodes::new();
        assert_eq!(nodes.insert("\\a".into(), 2).unwrap(), INodeNo(2));
        nodes.lookup(INodeNo(2));
        assert!(nodes.insert("\\b".into(), 2).is_err());
        assert_eq!(nodes.insert("\\a".into(), 2).unwrap(), INodeNo(2));
    }
    #[test]
    fn errors_retain_native_meaning() {
        assert!(status(NtStatus::SUCCESS).is_ok());
        assert_eq!(status(NtStatus::ACCESS_DENIED), Err(Errno::EACCES));
        assert_eq!(status(NtStatus::NO_SUCH_FILE), Err(Errno::ENOENT));
        assert_eq!(status(NtStatus::DISK_FULL), Err(Errno::ENOSPC));
        assert_eq!(status(NtStatus::from(0xcfffffff)), Err(Errno::EIO));
    }
    #[test]
    fn unmounted_or_nonfilesystem_announces_create_no_paths() {
        let runtime =
            std::env::temp_dir().join(format!("mios-rdp-storage-negative-{}", std::process::id()));
        assert!(!runtime.exists());
        let mut cfg = Config::default();
        cfg.runtime_dir = runtime.clone();
        cfg.redirect.drives = true;
        let stats = Arc::new(PipeStats::default());
        let mut devices = Devices::new();
        devices.add_drive(1, "files".into());
        let mut offline = Backend::new(cfg.clone(), Arc::clone(&stats), None);
        assert_eq!(
            offline.on_device_announce(&[devices.device(1).unwrap().clone()]),
            [(1, false)]
        );
        let (sender, _) = tokio::sync::mpsc::unbounded_channel();
        let mut connected = Backend::new(cfg, stats, Some(sender));
        assert_eq!(
            connected.on_device_announce(&[DeviceAnnounceHeader::new_smartcard(2)]),
            [(2, false)]
        );
        assert!(
            !runtime.exists(),
            "refused devices must not leave empty mount directories"
        );
    }
    #[test]
    fn inode_forget_keeps_live_lookup_and_open_handles() {
        let mut nodes = Nodes::new();
        let ino = nodes.insert("\\a".into(), 2).unwrap();
        nodes.lookup(ino);
        nodes.lookup(ino);
        nodes.open(ino);
        nodes.forget(ino, 1);
        assert!(nodes.paths.contains_key(&ino.0));
        nodes.forget(ino, 1);
        assert!(
            nodes.paths.contains_key(&ino.0),
            "an open file must survive dentry eviction"
        );
        assert!(nodes.insert("\\blocked".into(), 2).is_err());
        nodes.invalidate_path("\\a");
        assert_eq!(nodes.references.get(&ino.0), Some(&(0, 1)));
        assert!(
            nodes.insert("\\a".into(), 2).is_err(),
            "unlinked open handles count against the resource limit"
        );
        nodes.release(ino);
        let replacement = nodes.insert("\\a".into(), 2).unwrap();
        assert_ne!(ino, replacement);
        assert!(
            nodes.insert("\\b".into(), 2).is_ok(),
            "unused enumeration entries may be evicted"
        );
        assert_eq!(nodes.references.len(), 2);
    }
    #[test]
    fn transport_routes_completions_and_device_removal() {
        let (sender, mut events) = tokio::sync::mpsc::unbounded_channel();
        let io = Arc::new(Transport {
            sender,
            serial: Mutex::new(()),
            pending: Mutex::new(None),
            removed: Mutex::new(HashSet::new()),
            closed: AtomicBool::new(false),
            timeout: Duration::from_secs(2),
            stats: Arc::new(PipeStats::default()),
        });
        let wait_event = |events: &mut tokio::sync::mpsc::UnboundedReceiver<ServerEvent>| {
            let deadline = std::time::Instant::now() + Duration::from_secs(1);
            loop {
                if let Ok(event) = events.try_recv() {
                    return event;
                }
                assert!(
                    std::time::Instant::now() < deadline,
                    "request was not dispatched"
                );
                std::thread::sleep(Duration::from_millis(1));
            }
        };
        let worker = Arc::clone(&io);
        let first = std::thread::spawn(move || {
            worker.call(
                7,
                Kind::Read,
                Message::Read {
                    device_id: 7,
                    file_id: 1,
                    length: 3,
                    offset: 0,
                },
            )
        });
        assert!(matches!(
            wait_event(&mut events),
            ServerEvent::Rdpdr(Message::Read { device_id: 7, .. })
        ));
        io.complete(8, Response::Read(NtStatus::SUCCESS, vec![9]));
        assert!(io.pending.lock().unwrap().is_some());
        io.complete(7, Response::Read(NtStatus::SUCCESS, vec![1, 2, 3]));
        assert!(
            matches!(first.join().unwrap(),Ok(Response::Read(NtStatus::SUCCESS,data)) if data==[1,2,3])
        );
        let worker = Arc::clone(&io);
        let second = std::thread::spawn(move || {
            worker.call(
                7,
                Kind::Read,
                Message::Read {
                    device_id: 7,
                    file_id: 1,
                    length: 3,
                    offset: 0,
                },
            )
        });
        wait_event(&mut events);
        io.remove(7);
        assert!(matches!(second.join().unwrap(), Err(Errno::ENODEV)));
        assert!(matches!(
            io.call(
                7,
                Kind::Read,
                Message::Read {
                    device_id: 7,
                    file_id: 1,
                    length: 3,
                    offset: 0
                }
            ),
            Err(Errno::ENODEV)
        ));
        assert!(events.try_recv().is_err());
    }
    #[test]
    fn timeout_poisoning_prevents_late_reply_reuse() {
        let (sender, mut events) = tokio::sync::mpsc::unbounded_channel();
        let stats = Arc::new(PipeStats::default());
        let io = Transport {
            sender,
            serial: Mutex::new(()),
            pending: Mutex::new(None),
            removed: Mutex::new(HashSet::new()),
            closed: AtomicBool::new(false),
            timeout: Duration::from_millis(5),
            stats,
        };
        assert!(matches!(
            io.call(
                7,
                Kind::Read,
                Message::Read {
                    device_id: 7,
                    file_id: 1,
                    length: 10,
                    offset: 0
                }
            ),
            Err(Errno::ETIMEDOUT)
        ));
        assert!(events.try_recv().is_ok());
        io.complete(7, Response::Read(NtStatus::SUCCESS, vec![1]));
        assert!(matches!(
            io.call(
                7,
                Kind::Read,
                Message::Read {
                    device_id: 7,
                    file_id: 1,
                    length: 10,
                    offset: 0
                }
            ),
            Err(Errno::ENODEV)
        ));
        assert!(events.try_recv().is_err());
    }
}
