//! Read-only Linux example: trusted managed child, private grant custody,
//! typed search/context, optional one-event journal. No setup or installs.
#[cfg(not(target_os = "linux"))]
fn main() {
    eprintln!("This managed Desktop example requires Linux.");
    std::process::exit(2);
}

#[cfg(target_os = "linux")]
mod linux {
    use local_message_hub_rs::{
        desktop_client::{Client, Search, Window, journal},
        desktop_sdk::{Connection, Launch, Mode},
        plugins::AccountScope,
    };
    use rustix::fs::{FileType, fstat};
    use serde::{Deserialize, Deserializer};
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};
    use std::{
        cell::RefCell,
        fs,
        io::{self, BufRead, BufReader, Read, Write},
        path::{Path, PathBuf},
        process::{Child, Command, Stdio},
        sync::mpsc::{Receiver, sync_channel},
        thread,
        time::{Duration, Instant},
    };
    use zeroize::Zeroizing;

    const WAIT: Duration = Duration::from_secs(8);
    type Result<T> = std::result::Result<T, &'static str>;

    struct Private(Zeroizing<String>);

    impl<'de> Deserialize<'de> for Private {
        fn deserialize<D: Deserializer<'de>>(input: D) -> std::result::Result<Self, D::Error> {
            String::deserialize(input).map(|s| Self(Zeroizing::new(s)))
        }
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct Frame {
        reader_token: Private,
        search: Option<Private>,
        journal: Option<JournalFrame>,
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct JournalFrame {
        token: Private,
        previously_applied: Option<Private>,
        wait_one: bool,
    }

    fn hex64(value: &str) -> bool {
        value.len() == 64 && value.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }

    fn private_pipes() -> Result<()> {
        let input = fstat(io::stdin()).map_err(|_| "private input pipe unavailable")?;
        let output = fstat(io::stdout()).map_err(|_| "private output pipe unavailable")?;
        let anonymous = |fd: &str, inode: u64, mode| FileType::from_raw_mode(mode) == FileType::Fifo
            && fs::read_link(fd).is_ok_and(|link| link.as_os_str() == std::ffi::OsStr::new(
                &format!("pipe:[{inode}]")
            ));
        if input.st_ino == output.st_ino
            || !anonymous("/proc/self/fd/0", input.st_ino as u64, input.st_mode)
            || !anonymous("/proc/self/fd/1", output.st_ino as u64, output.st_mode) {
            return Err("use separate private anonymous stdin and stdout pipes");
        }
        Ok(())
    }

    fn read_private_frame() -> Result<Frame> {
        private_pipes()?;
        let (send, receive) = sync_channel(1);
        thread::Builder::new().spawn(move || {
            let result = (|| {
                let mut raw = Zeroizing::new(Vec::new());
                io::stdin().take(8193).read_to_end(&mut raw).map_err(|_| "input unavailable")?;
                let line = raw.strip_suffix(b"\n").filter(|s| raw.len() <= 8192 && !s.contains(&b'\n'))
                    .ok_or("expected one bounded private input frame and EOF")?;
                let frame: Frame = serde_json::from_slice(line).map_err(|_| "invalid private input frame")?;
                if !hex64(&frame.reader_token.0) || frame.journal.as_ref().is_some_and(|j| !hex64(&j.token.0)) {
                    return Err("invalid private input frame");
                }
                Ok(frame)
            })();
            let _ = send.send(result);
        }).map_err(|_| "private input reader unavailable")?;
        receive.recv_timeout(WAIT).map_err(|_| "private input frame timed out")?
    }

    fn verify_binary(path: &Path, expected: &str) -> Result<()> {
        if !hex64(expected) { return Err("expected a trusted SHA-256 pin"); }
        let mut file = fs::File::open(path).map_err(|_| "trusted binary unavailable")?;
        let meta = file.metadata().map_err(|_| "trusted binary unavailable")?;
        if !meta.is_file() || meta.len() > (512 << 20) { return Err("invalid trusted binary"); }
        let mut magic = [0; 4];
        file.read_exact(&mut magic).map_err(|_| "invalid trusted binary")?;
        if &magic != b"\x7fELF" { return Err("invalid trusted Linux ELF"); }
        let mut sha = Sha256::new();
        sha.update(magic);
        let mut left = meta.len() - 4;
        let mut buf = [0; 65536];
        while left != 0 {
            let end = buf.len().min(left as usize);
            file.read_exact(&mut buf[..end]).map_err(|_| "unstable trusted binary")?;
            sha.update(&buf[..end]);
            left -= end as u64;
        }
        if file.read(&mut buf[..1]).map_err(|_| "unstable trusted binary")? != 0 {
            return Err("unstable trusted binary");
        }
        if hex::encode(sha.finalize()) != expected { return Err("trusted binary digest mismatch"); }
        Ok(())
    }

    struct Owned {
        child: RefCell<Child>,
        launch: Launch,
        frames: Receiver<Vec<u8>>,
        ready: Vec<u8>,
        backend: u32,
    }

    impl Owned {
        fn start(launch: Launch) -> Result<Self> {
            let mut child = Command::new(launch.executable()).args(launch.args()).env_clear()
                .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null())
                .spawn().map_err(|_| "could not start owned supervisor")?;
            let (send, frames) = sync_channel(4);
            let stdout = child.stdout.take().expect("configured stdout pipe");
            thread::spawn(move || {
                let mut reader = BufReader::new(stdout);
                loop {
                    let mut line = Vec::new();
                    let read = (&mut reader).take(1025).read_until(b'\n', &mut line);
                    if !matches!(read, Ok(1..=1024)) || !line.ends_with(b"\n") || send.send(line).is_err() {
                        return;
                    }
                }
            });
            let mut owner = Self { child: RefCell::new(child), launch, frames, ready: Vec::new(), backend: 0 };
            owner.ready = owner.frames.recv_timeout(WAIT).map_err(|_| "managed readiness unavailable")?;
            owner.backend = owner.connection()?.backend_pid();
            Ok(owner)
        }

        fn connection(&self) -> Result<Connection> {
            self.launch.accept_ready(&self.ready).map_err(|_| "invalid managed readiness")
        }

        fn current(&self, pid: u32) -> bool {
            pid == self.backend && self.child.borrow_mut().try_wait().is_ok_and(|status| status.is_none())
        }

        fn stop(&self) -> Result<()> {
            self.child.borrow_mut().stdin.take().ok_or("owned stop pipe unavailable")?
                .write_all(b"stop\n").map_err(|_| "owned stop failed")?;
            let line = self.frames.recv_timeout(WAIT).map_err(|_| "owned stop response missing")?;
            let status: Value = serde_json::from_slice(&line).map_err(|_| "owned stop response invalid")?;
            if status != json!({"event":"stopped","forced":false}) { return Err("owned stop was not clean"); }
            let deadline = Instant::now() + WAIT;
            loop {
                match self.child.borrow_mut().try_wait().map_err(|_| "owned stop failed")? {
                    Some(status) if status.success() => return Ok(()),
                    Some(_) => return Err("owned supervisor stop failed"),
                    None if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
                    None => return Err("owned stop timed out"),
                }
            }
        }
    }

    impl Drop for Owned {
        fn drop(&mut self) {
            let child = self.child.get_mut();
            drop(child.stdin.take());
            let deadline = Instant::now() + WAIT;
            while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(10));
            }
            if matches!(child.try_wait(), Ok(None)) { let _ = child.kill(); }
            let _ = child.wait();
        }
    }

    pub(super) fn run() -> Result<Value> {
        let inputs: Vec<_> = std::env::args_os().skip(1).collect();
        let [binary, pin, archive, registry, platform, account, reader_id, extra @ ..] = inputs.as_slice()
            else { return Err("use BINARY SHA256 ARCHIVE PRIVATE_REGISTRY PLATFORM ACCOUNT READ_GRANT [JOURNAL_GRANT]"); };
        if extra.len() > 1 { return Err("expected at most one separate journal grant ID"); }
        let journal_id = extra.first().map(|s| s.to_str().ok_or("journal ID must be UTF-8")).transpose()?;
        let reader_id = reader_id.to_str().ok_or("reader ID must be UTF-8")?;
        if journal_id == Some(reader_id) { return Err("reader and journal grants must be distinct"); }
        let scope = AccountScope {
            platform: platform.to_str().ok_or("platform must be UTF-8")?.into(),
            account_id: account.to_str().ok_or("account must be UTF-8")?.into(),
        };
        let binary = PathBuf::from(binary);
        // Validate canonical no-symlink path components through the actual SDK
        // before opening or starting any fixture program.
        let launch = Launch::new(&binary, Mode::NativeLive, Path::new(archive), Path::new(registry))
            .map_err(|_| "invalid trusted launch paths")?;
        verify_binary(&binary, pin.to_str().ok_or("invalid binary pin")?)?;
        let frame = read_private_frame()?;
        if journal_id.is_some() != frame.journal.is_some() { return Err("journal grant and frame must agree"); }
        let owner = Owned::start(launch)?;
        let mut client = Client::connect_installed_reader(owner.connection()?,
            frame.reader_token.0, reader_id.into(), scope.clone(),
            |pid| owner.current(pid)).map_err(|_| "exact read-only grant discovery failed")?;
        let list = client.conversations(10, 0).map_err(|_| "conversation overview unavailable")?;
        let mut report = json!({"mode":"metadata_output_only","conversation_count":list.total});
        if let Some(query) = &frame.search {
            let found = client.search_messages(Search {
                text: &query.0, conversation_id: None, cursor: None, limit: 1
            }).map_err(|_| "typed search unavailable")?;
            report["search_matches"] = found.total.into();
            if let Some(hit) = found.items.first() {
                let context = client.message_context(&hit.message.conversation_id, &hit.message.id,
                    Window {before:1, after:1}).map_err(|_| "typed context unavailable")?;
                report["search_context"] = json!({
                    "target_index":context.target_index, "window_items":context.items.len(),
                });
            }
        }
        if let (Some(id), Some(input)) = (journal_id, frame.journal) {
            let mut journal = journal::Client::connect(owner.connection()?,
                input.token.0, id.into(), scope,
                |pid| owner.current(pid)).map_err(|_| "separate scoped journal grant unavailable")?;
            let head = journal.checkpoint().map_err(|_| "journal checkpoint unavailable")?;
            let after = match input.previously_applied.as_ref() {
                Some(saved) => journal.restore_cursor(&head, &saved.0).map_err(|_| "saved journal cursor rejected")?,
                None => journal.restore_cursor(&head, head.cursor().as_str()).map_err(|_| "journal cursor unavailable")?,
            };
            let observed = if input.wait_one {
                match journal.wait_one(&after).map_err(|_| "bounded journal wait unavailable")? {
                    journal::Wait::Item { event, next_cursor } => Some((event.event_id, next_cursor)),
                    journal::Wait::Idle => None,
                }
            } else {
                let page = journal.replay(&after, 1).map_err(|_| "bounded journal replay unavailable")?;
                page.items.first().map(|event| (event.event_id, page.next_cursor))
            };
            report["journal"] = match observed {
                Some((event_id, cursor)) => json!({
                    "event_id":event_id, "next_cursor_if_applied":cursor.as_str(), "applied":false
                }),
                None => json!({"event_available":false, "applied":false}),
            };
        }
        owner.stop()?;
        Ok(report)
    }
}

#[cfg(target_os = "linux")]
fn main() {
    match linux::run() {
        Ok(report) => println!("{report}"),
        Err(issue) => {
            eprintln!("managed-desktop: {issue}");
            std::process::exit(2);
        }
    }
}
