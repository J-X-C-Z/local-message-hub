//! Dedicated finite reader for the managed native event stream. None of
//! the ordinary HTTP response framing or archive routes use this parser.
use super::{Event, EventKind, Loopback, STREAM, WireCheckpoint, archive_id, position};
use crate::desktop_client::{Error, MAX_HEADERS, TIMEOUT, hex64, open, send};
use std::{
    io::{self, Read, Write},
    net::TcpStream,
    time::Instant,
};
use zeroize::Zeroizing;

const MAX_CHUNK: usize = 16 * 1024;
const MAX_FRAME: usize = 8 * 1024;
const MAX_WIRE: usize = 64 * 1024;

pub(super) fn wait_one(wire: &mut Loopback<'_>, after: &str, namespace: &str, ordinal: i64)
    -> Result<Option<(Event, String)>, Error> {
    wire.live()?;
    let address = wire.connection.address();
    let deadline = Instant::now() + TIMEOUT;
    let mut socket = open(address, deadline)?;
    wire.live()?;
    let mut query = url::form_urlencoded::Serializer::new(String::new());
    query.append_pair("cursor", after);
    let mut request = Zeroizing::new(Vec::with_capacity(512));
    write!(&mut request, "GET {STREAM}?{} HTTP/1.1\r\nHost: {address}\r\nAccept: text/event-stream\r\nConnection: close\r\n",
        query.finish()).map_err(|_| Error::Transport)?;
    wire.grant.with_token(|token| {
        if !hex64(token) { return Err(Error::Identity); }
        write!(&mut request, "Authorization: Bearer {token}\r\n\r\n").map_err(|_| Error::Transport)
    })?;
    send(&mut socket, &request, deadline)?;
    drop(request);
    let mut input = Chunked::new(socket, deadline)?;
    let checkpoint = input.frame()?.ok_or(Error::Transport)?;
    let initial = Fields::parse(&checkpoint)?;
    if initial.name != "checkpoint" || initial.id != after || initial.retry != Some("2000") {
        return Err(Error::Protocol);
    }
    let body: WireCheckpoint = serde_json::from_str(initial.data).map_err(|_| Error::Protocol)?;
    if body.checkpoint != ordinal || body.cursor != after { return Err(Error::Protocol); }
    loop {
        let Some(frame) = input.frame()? else {
            wire.live()?;
            return Ok(None);
        };
        // Only the server's named, body-free heartbeat is accepted. It
        // cannot extend the deadline or create/update an applied position.
        if frame.as_slice() == b": keep-alive\n\n" { continue; }
        let next = Fields::parse(&frame)?;
        if next.retry.is_some() { return Err(Error::Protocol); }
        let event: Event = serde_json::from_str(next.data).map_err(|_| Error::Protocol)?;
        if event.platform != wire.scope.platform || event.account_id != wire.scope.account_id {
            return Err(Error::Identity);
        }
        let name = match event.kind {
            EventKind::Created => "message.created",
            EventKind::Revised => "message.revised",
        };
        if name != next.name || ordinal.checked_add(1) != Some(event.event_id)
            || position(next.id) != Some((namespace, event.event_id))
            || !archive_id(&event.conversation_id) || !archive_id(&event.message_id) || event.timestamp < 0
        {
            return Err(Error::Protocol);
        }
        wire.live()?;
        return Ok(Some((event, next.id.to_owned())));
    }
}

struct Fields<'a> {
    name: &'a str,
    id: &'a str,
    data: &'a str,
    retry: Option<&'a str>,
}

impl<'a> Fields<'a> {
    fn parse(frame: &'a [u8]) -> Result<Self, Error> {
        let text = std::str::from_utf8(frame).map_err(|_| Error::Protocol)?;
        let text = text.strip_suffix("\n\n").ok_or(Error::Protocol)?;
        let (mut name, mut id, mut data, mut retry) = (None, None, None, None);
        for line in text.split('\n') {
            let (key, value) = line.split_once(": ").ok_or(Error::Protocol)?;
            if value.is_empty() || value.chars().any(char::is_control) { return Err(Error::Protocol); }
            let target = match key {
                "event" => &mut name,
                "id" => &mut id,
                "data" => &mut data,
                "retry" => &mut retry,
                _ => return Err(Error::Protocol),
            };
            if target.replace(value).is_some() { return Err(Error::Protocol); }
        }
        Ok(Self { name: name.ok_or(Error::Protocol)?, id: id.ok_or(Error::Protocol)?,
            data: data.ok_or(Error::Protocol)?, retry })
    }
}

struct Wire {
    socket: TcpStream,
    buffer: Zeroizing<[u8; 1024]>,
    offset: usize,
    available: usize,
    available_budget: usize,
    deadline: Instant,
}

impl Wire {
    // None means the one absolute deadline elapsed, never EOF.
    fn byte(&mut self) -> Result<Option<u8>, Error> {
        loop {
            let remaining = self.deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return if self.offset == self.available { Ok(None) } else { Err(Error::Transport) };
            }
            if self.offset < self.available {
                if self.available_budget == 0 { return Err(Error::TooLarge); }
                self.available_budget -= 1;
                let byte = self.buffer[self.offset];
                self.offset += 1;
                return Ok(Some(byte));
            }
            self.socket.set_read_timeout(Some(remaining)).map_err(|_| Error::Transport)?;
            match self.socket.read(&mut self.buffer[..]) {
                Ok(0) => return Err(Error::Transport),
                Ok(n) => {
                    self.offset = 0;
                    self.available = n;
                }
                Err(e) if matches!(e.kind(), io::ErrorKind::Interrupted | io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock) => {}
                Err(_) => return Err(Error::Transport),
            }
        }
    }

    fn required(&mut self) -> Result<u8, Error> { self.byte()?.ok_or(Error::Transport) }
}

struct Chunked {
    wire: Wire,
    left: usize,
}

impl Chunked {
    fn new(socket: TcpStream, deadline: Instant) -> Result<Self, Error> {
        let mut wire = Wire { socket, buffer: Zeroizing::new([0; 1024]), offset: 0,
            available: 0, available_budget: MAX_WIRE, deadline };
        let mut head = Zeroizing::new(Vec::with_capacity(1024));
        loop {
            head.push(wire.required()?);
            if head.ends_with(b"\r\n\r\n") { break; }
            if head.len() >= MAX_HEADERS + 4 { return Err(Error::TooLarge); }
        }
        validate_headers(&head[..head.len()-4])?;
        Ok(Self { wire, left: 0 })
    }

    fn body_byte(&mut self) -> Result<Option<u8>, Error> {
        if self.left == 0 {
            let Some(mut byte) = self.wire.byte()? else { return Ok(None); };
            let mut size = 0usize;
            let mut digits = 0u8;
            while byte != b'\r' {
                if digits == 4 || !byte.is_ascii_hexdigit() { return Err(Error::Protocol); }
                let value = (byte as char).to_digit(16).ok_or(Error::Protocol)? as usize;
                size = size * 16 + value;
                digits += 1;
                byte = self.wire.required()?;
            }
            if digits == 0 || self.wire.required()? != b'\n' { return Err(Error::Protocol); }
            if size > MAX_CHUNK { return Err(Error::TooLarge); }
            // A terminated or drained stream is not a clean timer expiry.
            if size == 0 { return Err(Error::Transport); }
            self.left = size;
        }
        let byte = self.wire.required()?;
        self.left -= 1;
        if self.left == 0 {
            if self.wire.required()? != b'\r' || self.wire.required()? != b'\n' {
                return Err(Error::Protocol);
            }
        }
        Ok(Some(byte))
    }

    fn frame(&mut self) -> Result<Option<Zeroizing<Vec<u8>>>, Error> {
        let mut payload = Zeroizing::new(Vec::with_capacity(1024));
        loop {
            let Some(byte) = self.body_byte()? else {
                return if payload.is_empty() { Ok(None) } else { Err(Error::Transport) };
            };
            payload.push(byte);
            if payload.ends_with(b"\n\n") { return Ok(Some(payload)); }
            if payload.len() >= MAX_FRAME { return Err(Error::TooLarge); }
        }
    }
}

fn validate_headers(bytes: &[u8]) -> Result<(), Error> {
    let text = std::str::from_utf8(bytes).map_err(|_| Error::Protocol)?;
    if !text.is_ascii() { return Err(Error::Protocol); }
    let mut lines = text.split("\r\n");
    let line = lines.next().and_then(|line| line.strip_prefix("HTTP/1.1 ")).ok_or(Error::Protocol)?;
    let (code, reason) = line.split_once(' ').ok_or(Error::Protocol)?;
    if code.len() != 3 || !code.bytes().all(|b| b.is_ascii_digit())
        || reason.bytes().any(|b| !matches!(b, 0x20..=0x7e)) { return Err(Error::Protocol); }
    let code = code.parse::<u16>().map_err(|_| Error::Protocol)?;
    if (400..=599).contains(&code) { return Err(Error::Status(code)); }
    if code != 200 { return Err(Error::Protocol); }
    let (mut chunked, mut sse, mut encoding) = (false, false, false);
    for line in lines {
        let (key, value) = line.split_once(':').ok_or(Error::Protocol)?;
        if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
            || value.bytes().any(|b| !(b == b'\t' || matches!(b, 0x20..=0x7e))) {
            return Err(Error::Protocol);
        }
        let value = value.trim_matches([' ', '\t']);
        if key.eq_ignore_ascii_case("transfer-encoding") {
            if chunked || !value.eq_ignore_ascii_case("chunked") { return Err(Error::Protocol); }
            chunked = true;
        } else if key.eq_ignore_ascii_case("content-type") {
            if sse || !value.eq_ignore_ascii_case("text/event-stream") { return Err(Error::Protocol); }
            sse = true;
        } else if key.eq_ignore_ascii_case("content-encoding") {
            if encoding || !value.eq_ignore_ascii_case("identity") { return Err(Error::Protocol); }
            encoding = true;
        } else if ["content-length", "content-range", "trailer", "upgrade"].iter().any(|h| key.eq_ignore_ascii_case(h)) {
            return Err(Error::Protocol);
        }
    }
    if !chunked || !sse { return Err(Error::Protocol); }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{net::{Ipv4Addr, TcpListener}, thread, time::Duration};

    const HEADER: &[u8] = b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n";

    fn read_frames(parts: &[&[u8]], read: impl FnOnce(&mut Chunked)) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let socket = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
        let (mut sender, _) = listener.accept().unwrap();
        let mut payload = HEADER.to_vec();
        for part in parts {
            write!(payload, "{:x}\r\n", part.len()).unwrap();
            payload.extend_from_slice(part);
            payload.extend_from_slice(b"\r\n");
        }
        sender.write_all(&payload).unwrap();
        read(&mut Chunked::new(socket, Instant::now() + Duration::from_secs(1)).unwrap());
        drop(sender);
    }

    #[test]
    fn rejects_ambiguous_response_headers_and_any_redirect() {
        for head in [
            &b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nContent-Length: 2"[..],
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nTransfer-Encoding: chunked",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: identity, chunked",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 2",
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\nTrailer: extra",
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked",
            b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/",
        ] {
            assert!(matches!(validate_headers(head), Err(Error::Protocol)));
        }
        assert!(matches!(validate_headers(b"HTTP/1.1 403 Forbidden"), Err(Error::Status(403))));
    }

    #[test]
    fn chunk_and_sse_boundaries_are_independent() {
        read_frames(&[b"event: check", b"point\nid: token\n", b"retry: 2000\ndata: {}\n",
            b"\n: keep-", b"alive\n\nevent: message.created\nid: next\ndata: {}\n\n"], |stream| {
            let split = stream.frame().unwrap().unwrap();
            let first = Fields::parse(&split).unwrap();
            assert_eq!((first.name, first.id, first.retry, first.data), ("checkpoint", "token", Some("2000"), "{}"));
            assert_eq!(stream.frame().unwrap().unwrap().as_slice(), b": keep-alive\n\n");
            let next = stream.frame().unwrap().unwrap();
            assert_eq!(Fields::parse(&next).unwrap().name, "message.created");
        });
        assert!(matches!(Fields::parse(b"id: one\nid: two\nevent: x\ndata: {}\n\n"), Err(Error::Protocol)));
    }

    #[test]
    fn clean_deadline_differs_from_partial_frame_and_eof() {
        fn timed(partial: &[u8]) -> Result<Option<Zeroizing<Vec<u8>>>, Error> {
            let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
            let receiver = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
            let (mut sender, _) = listener.accept().unwrap();
            sender.write_all(HEADER).unwrap();
            sender.write_all(partial).unwrap();
            let mut chunked = Chunked::new(receiver, Instant::now() + Duration::from_millis(60)).unwrap();
            let result = chunked.frame();
            drop(sender);
            result
        }
        assert!(timed(b"").unwrap().is_none());
        assert!(matches!(timed(b"3\r\neve\r\n"), Err(Error::Transport)));
        assert!(matches!(timed(b"3\r\nev"), Err(Error::Transport)));
        assert!(matches!(timed(b"3;extension=1\r\n"), Err(Error::Protocol)));
        assert!(matches!(timed(b"4001\r\n"), Err(Error::TooLarge)));
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (mut socket, _) = listener.accept().unwrap();
            socket.write_all(HEADER).unwrap();
            socket.write_all(b"0\r\n\r\n").unwrap();
        });
        let socket = TcpStream::connect(address).unwrap();
        let mut chunked = Chunked::new(socket, Instant::now() + Duration::from_secs(1)).unwrap();
        assert!(matches!(chunked.frame(), Err(Error::Transport)));
        server.join().unwrap();
    }
}
