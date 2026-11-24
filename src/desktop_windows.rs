//! Windows snapshot and ordinary native-live owner pipes. Not a Job Object supervisor.
use std::{
    ffi::c_void,
    fs,
    io::{self, Read, Write},
    os::windows::fs::MetadataExt,
    path::{Component, Path, PathBuf, Prefix},
    process::{Child, ChildStdin},
    sync::mpsc,
    thread,
};

const START: [u8; 1] = [0xb7];
const FILE_TYPE_PIPE: u32 = 3;
const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
const DRIVE_FIXED: u32 = 3;
const STD_INPUT_HANDLE: u32 = (-10_i32) as u32;
const STD_OUTPUT_HANDLE: u32 = (-11_i32) as u32;
pub(crate) const CREATE_NO_WINDOW: u32 = 0x0800_0000;

#[link(name = "kernel32")]
unsafe extern "system" {
    #[link_name = "GetStdHandle"]
    fn get_std_handle(which: u32) -> *mut c_void;
    #[link_name = "GetFileType"]
    fn get_file_type(handle: *mut c_void) -> u32;
    #[link_name = "GetDriveTypeW"]
    fn get_drive_type(root: *const u16) -> u32;
}

fn issue(message: &'static str) -> io::Error { io::Error::other(message) }
fn path_issue() -> io::Error { issue("expected a trusted fixed-volume path of the requested kind") }

pub(crate) fn local_file(path: &Path) -> io::Result<()> { local_path(path, false) }

/// An already provisioned ordinary archive, not a request to create or migrate one.
pub(crate) fn local_directory(path: &Path) -> io::Result<()> { local_path(path, true) }

fn local_path(path: &Path, directory: bool) -> io::Result<()> {
    let mut components = path.components().peekable();
    let Some(Component::Prefix(prefix)) = components.next() else { return Err(path_issue()); };
    let Prefix::Disk(drive) = prefix.kind() else { return Err(path_issue()); };
    if !drive.is_ascii_alphabetic() || components.next() != Some(Component::RootDir)
        || components.peek().is_none() {
        return Err(path_issue());
    }
    let root = [u16::from(drive), u16::from(b':'), u16::from(b'\\'), 0];
    // `root` is a live null-terminated UTF-16 "X:\" buffer.
    if unsafe { get_drive_type(root.as_ptr()) } != DRIVE_FIXED {
        return Err(path_issue());
    }
    let mut prefix = PathBuf::from(format!("{}:\\", char::from(drive)));
    while let Some(component) = components.next() {
        let Component::Normal(name) = component else { return Err(path_issue()); };
        let name = name.to_str().ok_or_else(path_issue)?;
        if name.ends_with(' ') || name.ends_with('.') || name.contains(':') || name.chars().any(char::is_control) {
            return Err(path_issue());
        }
        prefix.push(name);
        let meta = fs::symlink_metadata(&prefix).map_err(|_|path_issue())?;
        let right_kind = if components.peek().is_some() || directory { meta.is_dir() } else { meta.is_file() };
        if meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 || !right_kind {
            return Err(path_issue());
        }
    }
    Ok(())
}

fn require_pipe(which: u32) -> io::Result<()> {
    // These are borrowed process stdio handles. Never close or assume ownership.
    let handle = unsafe { get_std_handle(which) };
    if handle.is_null() || handle as isize == -1 || unsafe { get_file_type(handle) } != FILE_TYPE_PIPE {
        return Err(issue("desktop supervision requires private pipes"));
    }
    Ok(())
}

/// Windows reports both anonymous and named pipes as FILE_TYPE_PIPE. Only a
/// trusted parent can ensure privately held anonymous frontend endpoints.
pub(crate) fn require_frontend_pipes() -> io::Result<()> {
    require_pipe(STD_INPUT_HANDLE)?;
    require_pipe(STD_OUTPUT_HANDLE)
}

pub(crate) struct Owner { _writer: ChildStdin }
impl Owner {
    pub(crate) fn arm(child: &mut Child) -> io::Result<Self> {
        let mut writer = child.stdin.take().ok_or_else(||issue("desktop owner pipe unavailable"))?;
        writer.write_all(&START).map_err(|_|issue("snapshot child could not be armed"))?;
        Ok(Self { _writer:writer })
    }
}

pub(crate) fn arm_owner_pipe() -> io::Result<()> {
    require_frontend_pipes()?;
    let (armed, response) = mpsc::sync_channel::<()>(0);
    thread::Builder::new().name("snapshot-owner".into()).spawn(move || {
        let mut channel = io::stdin().lock();
        let mut token = [0_u8;1];
        if channel.read_exact(&mut token).is_err() || token != START || armed.send(()).is_err() {
            return;
        }
        // This is voluntary EOF shutdown, not OS-enforced process termination.
        let code = if matches!(channel.read(&mut token),Ok(0)) { 0 } else { 1 };
        std::process::exit(code);
    }).map_err(|_|issue("snapshot owner watchdog unavailable"))?;
    response.recv().map_err(|_|issue("snapshot owner pipe was not armed"))
}


pub(crate) const LIVE_DRAIN_LIMIT: std::time::Duration = std::time::Duration::from_secs(6);

/// No console control event or PID lookup. The ordinary live child closes HTTP
/// admission on owner loss and drains until its independent watchdog expires.
/// Snapshot shutdown deliberately keeps its existing immediate-exit contract.
pub(crate) fn arm_live_owner_pipe() -> io::Result<tokio::sync::watch::Receiver<bool>> {
    require_frontend_pipes()?;
    let (signal, receiver) = tokio::sync::watch::channel(false);
    let (armed, response) = mpsc::sync_channel::<()>(0);
    thread::Builder::new().name("ordinary-live-owner".into()).spawn(move || {
        if live_until_owner_loss(io::stdin().lock(), armed, signal) {
            thread::sleep(LIVE_DRAIN_LIMIT);
            // A lost receipt is not proof of rollback. SQLite retains ordinary
            // transaction recovery; a hung or suspended child is not a clean stop.
            std::process::exit(75);
        }
    }).map_err(|_|issue("live owner watchdog unavailable"))?;
    response.recv().map_err(|_|issue("live owner pipe was not armed"))?;
    Ok(receiver)
}

/// Returns true only after the startup byte was accepted and owner loss was
/// published. Unexpected post-arm bytes and read errors fail closed like EOF.
fn live_until_owner_loss(mut input: impl Read, armed: mpsc::SyncSender<()>,
    signal: tokio::sync::watch::Sender<bool>) -> bool {
    let mut token = [0_u8; 1];
    if input.read_exact(&mut token).is_err() || token != START || armed.send(()).is_err() {
        return false;
    }
    loop {
        match input.read(&mut token) {
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            _ => break,
        }
    }
    signal.send_replace(true);
    true
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn live_owner_eof_and_unexpected_bytes_close_admission() {
        for bytes in [vec![START[0]], vec![START[0], 1]] {
            let (armed, ready) = mpsc::sync_channel(1);
            let (signal, watch) = tokio::sync::watch::channel(false);
            assert!(live_until_owner_loss(Cursor::new(bytes), armed, signal));
            assert_eq!(ready.try_recv(), Ok(()));
            assert!(*watch.borrow());
        }
    }

    #[test]
    fn live_owner_rejects_missing_or_wrong_start_without_readiness() {
        for bytes in [vec![], vec![0], vec![1, START[0]]] {
            let (armed, ready) = mpsc::sync_channel(1);
            let (signal, watch) = tokio::sync::watch::channel(false);
            assert!(!live_until_owner_loss(Cursor::new(bytes), armed, signal));
            assert!(ready.try_recv().is_err());
            assert!(!*watch.borrow());
            assert!(watch.has_changed().is_err());
        }
    }

    #[test]
    fn live_owner_read_error_closes_admission_after_handshake() {
        struct Input(u8);
        impl Read for Input {
            fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
                self.0 += 1;
                match self.0 {
                    1 => { output[0] = START[0]; Ok(1) },
                    2 => Err(io::ErrorKind::Interrupted.into()),
                    _ => Err(io::ErrorKind::BrokenPipe.into()),
                }
            }
        }
        let (armed, ready) = mpsc::sync_channel(1);
        let (signal, watch) = tokio::sync::watch::channel(false);
        assert!(live_until_owner_loss(Input(0), armed, signal));
        assert_eq!(ready.try_recv(), Ok(()));
        assert!(*watch.borrow());
    }

    #[test]
    fn windows_archive_requires_an_existing_directory_not_a_file() {
        let root = tempfile::tempdir().unwrap();
        let file = root.path().join("fixture.sqlite3");
        fs::write(&file, b"not an archive").unwrap();
        assert!(local_directory(root.path()).is_ok());
        assert!(local_file(&file).is_ok());
        assert!(local_directory(&file).is_err());
        assert!(local_file(root.path()).is_err());
        assert!(local_directory(&root.path().join("absent")).is_err());
    }

    #[test]
    fn windows_archive_rejects_nonordinary_path_spellings() {
        for path in [r"relative", r"C:relative", r"C:\", r"\\server\share\archive",
            r"\\?\C:\archive", r"C:\archive:stream", r"C:\archive.", r"C:\archive "] {
            assert!(local_directory(Path::new(path)).is_err(), "{path}");
        }
    }
}
