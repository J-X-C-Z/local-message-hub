//! macOS snapshot child ownership via a private inherited anonymous pipe.
use rustix::fs::{FileType, fstat};
use std::{
    io::{self, Read, Write},
    os::fd::AsFd,
    process::{Child, ChildStdin},
    thread,
};

const START: [u8; 1] = [0xb7];

fn issue(message: &'static str) -> io::Error { io::Error::other(message) }

fn require_pipe(fd: impl AsFd) -> io::Result<()> {
    let mode = fstat(fd).map_err(|_| issue("desktop pipe unavailable"))?.st_mode;
    if FileType::from_raw_mode(mode as _) != FileType::Fifo {
        return Err(issue("desktop supervision requires private pipes"));
    }
    Ok(())
}

/// This check rejects files, terminals, and sockets. Only the caller can
/// ensure its pipe endpoints are anonymous and are not inherited elsewhere.
pub(crate) fn require_frontend_pipes() -> io::Result<()> {
    require_pipe(io::stdin())?;
    require_pipe(io::stdout())
}

/// Only the supervisor owns this end. Dropping it signals an intentional
/// stop, and the OS closes it even when the supervisor exits abnormally.
pub(crate) struct Owner {
    _writer: ChildStdin,
}
impl Owner {
    pub(crate) fn arm(child: &mut Child) -> io::Result<Self> {
        let mut writer = child.stdin.take().ok_or_else(|| issue("desktop owner pipe unavailable"))?;
        writer.write_all(&START).map_err(|_| issue("snapshot child could not be armed"))?;
        Ok(Self { _writer: writer })
    }
}

/// No PID lookup, named rendezvous, file, token, or signal. The supervisor
/// must send exactly the start byte. No application data traverse this pipe.
pub(crate) fn arm_owner_pipe() -> io::Result<()> {
    require_pipe(io::stdin())?;
    let (armed, response) = std::sync::mpsc::sync_channel::<()>(0);
    thread::Builder::new().name("snapshot-owner".into()).spawn(move || {
        let mut channel = io::stdin().lock();
        let mut token = [0_u8; 1];
        if channel.read_exact(&mut token).is_err() || token != START {
            return; // disconnect the arm channel; serving has not begun
        }
        if armed.send(()).is_err() { return; }
        // The private pipe's EOF (including abnormal parent termination)
        // ends the process even if normal request handling is blocked.
        let code = match channel.read(&mut token) {
            Ok(0) => 0,
            _ => 1,
        };
        std::process::exit(code);
    }).map_err(|_| issue("snapshot owner watchdog unavailable"))?;
    response.recv().map_err(|_| issue("snapshot owner pipe was not armed"))
}

pub(crate) const LIVE_DRAIN_LIMIT:std::time::Duration=std::time::Duration::from_secs(6);

/// Ordinary live only. Owner loss first closes admission, then allows a
/// bounded drain. This watchdog is independent of the HTTP runtime.
pub(crate) fn arm_live_owner_pipe()->io::Result<tokio::sync::watch::Receiver<bool>> {
    require_pipe(io::stdin())?;
    let (signal,receiver)=tokio::sync::watch::channel(false);
    let (armed,response)=std::sync::mpsc::sync_channel::<()>(0);
    thread::Builder::new().name("ordinary-live-owner".into()).spawn(move||{
        let mut input=io::stdin().lock();
        let mut token=[0_u8;1];
        if input.read_exact(&mut token).is_err()||token!=START {return;}
        if armed.send(()).is_err(){return;}
        loop {
            match input.read(&mut token) {
                Err(e) if e.kind()==io::ErrorKind::Interrupted=>continue,
                _=>break, // EOF or unexpected control bytes both close admission
            }
        }
        signal.send_replace(true);
        thread::sleep(LIVE_DRAIN_LIMIT);
        // On a hung runtime the operating system must close the connection.
        // SQLite handles rollback/recovery; clients may have lost a receipt.
        std::process::exit(75);
    }).map_err(|_|issue("live owner watchdog unavailable"))?;
    response.recv().map_err(|_|issue("live owner pipe was not armed"))?;
    Ok(receiver)
}
