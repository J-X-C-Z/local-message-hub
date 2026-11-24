use ring::pbkdf2;
use std::{num::NonZeroU32,sync::{Arc,Mutex},time::{Duration,Instant}};

// Pinned Python account_safety.py record parameters; no normalization.
pub(crate) const ITERATIONS: u32 = 310_000;
const ROUNDS: NonZeroU32 = NonZeroU32::new(ITERATIONS).unwrap();
pub(crate) const LEASE: Duration = Duration::from_secs(900);

pub(crate) fn valid_password(password: &str) -> bool {
    (12..=256).contains(&password.chars().count())
}

#[derive(Clone,PartialEq,Eq)]
pub(crate) struct Verifier {
    pub salt: [u8;16],
    pub digest: [u8;32],
}
impl Verifier {
    pub fn new(password: &str) -> Option<Self> {
        if !valid_password(password) { return None; }
        let mut salt=[0;16];
        getrandom::fill(&mut salt).ok()?;
        let mut digest=[0;32];
        pbkdf2::derive(pbkdf2::PBKDF2_HMAC_SHA256,ROUNDS,&salt,password.as_bytes(),&mut digest);
        Some(Self {salt,digest})
    }
    pub fn verify(&self,password: &str) -> bool {
        valid_password(password) && pbkdf2::verify(
            pbkdf2::PBKDF2_HMAC_SHA256,ROUNDS,&self.salt,password.as_bytes(),&self.digest).is_ok()
    }
}

#[derive(Clone,Default)]
pub struct Gate(Arc<Mutex<State>>);
#[derive(Default)]
struct State { generation: u64, until: Option<Instant> }
impl Gate {
    pub fn lock(&self) {
        if let Ok(mut state) = self.0.lock() { state.until=None; }
    }
    pub(crate) fn unlock(&self) -> bool {
        let Ok(mut state) = self.0.lock() else {return false};
        let Some(next) = state.generation.checked_add(1) else {state.until=None;return false};
        state.generation=next;
        state.until=Some(Instant::now()+LEASE);
        true
    }
    pub(crate) fn generation(&self) -> Option<u64> {
        let state=self.0.lock().ok()?;
        (state.until? > Instant::now()).then_some(state.generation)
    }
    /// Metadata for the private browser supervisor. Does not renew the guard.
    #[cfg(target_os = "linux")]
    pub(crate) fn window_at(&self, now: Instant) -> Option<(u64, Instant)> {
        let state=self.0.lock().ok()?;
        let until=state.until?;
        (until>now).then_some((state.generation,until))
    }
}

#[derive(Default)]
pub struct Attempts {
    failures: Vec<Instant>,
    until: Option<Instant>,
}
impl Attempts {
    pub fn blocked(&self,now: Instant) -> bool { self.until.is_some_and(|end| now < end) }
    pub fn failed(&mut self,now: Instant) {
        const WINDOW: Duration = Duration::from_secs(60);
        self.failures.retain(|time| now.saturating_duration_since(*time) < WINDOW);
        self.failures.push(now);
        if self.failures.len() >= 5 {
            self.until=Some(now+WINDOW);
            self.failures.clear();
        }
    }
    pub fn clear(&mut self) { self.failures.clear(); self.until=None; }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn revoke_reunlock_and_expire_never_reuse_generation() {
        let gate=Gate::default();
        assert!(gate.generation().is_none());
        assert!(gate.unlock());
        assert_eq!(gate.generation(),Some(1));
        gate.lock();
        assert!(gate.generation().is_none());
        assert!(gate.unlock());
        assert_eq!(gate.generation(),Some(2));
        gate.0.lock().unwrap().until=Some(Instant::now());
        assert!(gate.generation().is_none());
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn guarded_lease_is_exclusive_and_requires_valid_password_for_import() {
        use std::{fs,os::unix::fs::PermissionsExt};
        let directory=tempfile::tempdir().unwrap();
        fs::set_permissions(directory.path(),fs::Permissions::from_mode(0o700)).unwrap();
        let passphrase="synthetic guard fixture phrase";
        crate::events::EventStore::init_guarded(directory.path(),passphrase).unwrap();
        let owner=GuardedExclusive::acquire(directory.path()).unwrap();
        assert!(GuardedExclusive::acquire(directory.path()).is_err());
        assert!(owner.authorize_import("synthetic wrong fixture phrase").is_err());
        let authorized=owner.authorize_import(passphrase).unwrap();
        assert!(authorized.recheck().is_ok());
        assert!(owner.archive().ingest("fixture.producer",crate::events::IngestRequest {
            idempotency_key:"never-write".into(),messages:Vec::new(),
        }).is_err());
        drop(owner);
        assert!(GuardedExclusive::acquire(directory.path()).is_ok());
    }

    #[test]
    fn five_recent_failures_gate_attempts_for_one_minute() {
        let mut attempts=Attempts::default();
        let start=Instant::now();
        for _ in 0..4 { attempts.failed(start); }
        assert!(!attempts.blocked(start));
        attempts.failed(start);
        assert!(attempts.blocked(start+Duration::from_secs(59)));
        assert!(!attempts.blocked(start+Duration::from_secs(60)));
        attempts.clear();
        assert!(!attempts.blocked(start));
    }
}

/// Linux coordination between guarded commands. The caller must use a
/// trusted local hierarchy; file ownership checks are not a same-UID boundary.
#[cfg(target_os = "linux")]
pub(crate) struct GuardedExclusive {
    archive: crate::events::EventStore,
    file: std::fs::File,
}

#[cfg(target_os = "linux")]
impl GuardedExclusive {
    pub(crate) fn acquire(directory: &std::path::Path) -> std::io::Result<Self> {
        use rustix::fs::{flock,open,FlockOperation,Mode,OFlags};
        let unavailable=||std::io::Error::other("guarded archive unavailable");
        let archive=crate::events::EventStore::open_guarded(directory).map_err(|_|unavailable())?;
        let file:std::fs::File=open(archive.path(),
            OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC,Mode::empty())
            .map_err(|_|unavailable())?.into();
        flock(&file,FlockOperation::NonBlockingLockExclusive)
            .map_err(|_|std::io::Error::other("guarded archive busy or does not support exclusive locking"))?;
        let result=Self{archive,file};
        result.verify()?;
        Ok(result)
    }

    fn verify(&self) -> std::io::Result<()> {
        use std::{fs,io,os::unix::fs::MetadataExt};
        let unavailable=||io::Error::other("guarded archive unavailable");
        let opened=self.file.metadata().map_err(|_|unavailable())?;
        let current=fs::symlink_metadata(self.archive.path()).map_err(|_|unavailable())?;
        if !opened.is_file() || !current.is_file() || opened.nlink()!=1
            || opened.mode()&0o077 != 0 || current.mode()&0o077 != 0
            || opened.dev()!=current.dev() || opened.ino()!=current.ino()
        { return Err(unavailable()); }
        self.archive.verify().map_err(|_|unavailable())
    }

    pub(crate) fn archive(&self) -> &crate::events::EventStore { &self.archive }

    pub(crate) fn authorize_import(&self,password:&str) -> std::io::Result<AuthorizedGuardedImport<'_>> {
        self.verify()?;
        if !self.archive.verify_guarded_password(password).unwrap_or(false) {
            return Err(std::io::Error::other("guarded authorization refused"));
        }
        self.verify()?;
        Ok(AuthorizedGuardedImport(self))
    }
}

#[cfg(target_os = "linux")]
pub(crate) struct AuthorizedGuardedImport<'a>(&'a GuardedExclusive);

#[cfg(target_os = "linux")]
impl AuthorizedGuardedImport<'_> {
    pub(crate) fn archive(&self) -> &crate::events::EventStore { self.0.archive() }

    pub(crate) fn recheck(&self) -> std::io::Result<()> { self.0.verify() }
}
