use std::{
    fs,
    io::{self, Read},
    path::{Path, PathBuf},
};

const MAX_SETTINGS_BYTES: u64 = 256 * 1024;

#[derive(thiserror::Error, Debug)]
pub enum SnapshotError {
    #[error("only a standalone regular index.sqlite3 snapshot is supported")]
    NotSnapshot,
    #[error("a Hub profile or SQLite journal was found beside the snapshot")]
    LiveProfile,
    #[error("the Hub account lock is configured")]
    AccountLock,
    #[error("failed to verify the snapshot directory: {0}")]
    Io(#[from] io::Error),
}

#[derive(Clone)]
pub struct Snapshot(PathBuf);

fn entry_exists(path: &Path) -> Result<bool, io::Error> {
    match fs::symlink_metadata(path) {
        Ok(_) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

impl Snapshot {
    pub fn open(path: impl AsRef<Path>) -> Result<Self, SnapshotError> {
        let path = path.as_ref();
        if path.file_name().and_then(std::ffi::OsStr::to_str) != Some("index.sqlite3")
            || !fs::symlink_metadata(path)?.is_file()
        {
            return Err(SnapshotError::NotSnapshot);
        }
        let result = Self(path.canonicalize()?);
        result.verify()?;
        Ok(result)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }

    pub fn verify(&self) -> Result<(), SnapshotError> {
        let parent = self.0.parent().ok_or(SnapshotError::NotSnapshot)?;
        if !fs::symlink_metadata(&self.0)?.is_file() || self.0.canonicalize()? != self.0 {
            return Err(SnapshotError::NotSnapshot);
        }
        for item in [
            "hub.sqlite3",
            "index.sqlite3-wal",
            "index.sqlite3-shm",
            "index.sqlite3-journal",
        ] {
            if entry_exists(&parent.join(item))? {
                return Err(SnapshotError::LiveProfile);
            }
        }
        let settings = parent.join("settings.json");
        if entry_exists(&settings)? {
            if fs::symlink_metadata(&settings)?.is_file() {
                let mut bytes = Vec::new();
                fs::File::open(settings)?
                    .take(MAX_SETTINGS_BYTES + 1)
                    .read_to_end(&mut bytes)?;
                if bytes.len() as u64 <= MAX_SETTINGS_BYTES
                    && serde_json::from_slice::<serde_json::Value>(&bytes)
                        .ok()
                        .is_some_and(|value| value.get("account_lock").is_some())
                {
                    return Err(SnapshotError::AccountLock);
                }
            }
            return Err(SnapshotError::LiveProfile);
        }
        Ok(())
    }
}
