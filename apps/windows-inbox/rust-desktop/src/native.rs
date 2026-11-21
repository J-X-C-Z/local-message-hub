use std::{ffi::OsStr, fs, io, os::windows::ffi::OsStrExt, path::Path, ptr};
use windows_sys::Win32::{
    Foundation::*,
    Security::Cryptography::*,
    System::{DataExchange::*, Memory::*},
    UI::{Controls::Dialogs::*, Shell::*, WindowsAndMessaging::*},
};

pub fn wide(value: impl AsRef<OsStr>) -> Vec<u16> {
    value.as_ref().encode_wide().chain([0]).collect()
}

pub fn message(hwnd: HWND, title: &str, text: &str, confirm: bool) -> bool {
    let style = if confirm {
        MB_OKCANCEL | MB_ICONQUESTION | MB_DEFBUTTON2
    } else {
        MB_OK | MB_ICONINFORMATION
    };
    unsafe { MessageBoxW(hwnd, wide(text).as_ptr(), wide(title).as_ptr(), style) == IDOK }
}

pub fn shell(hwnd: HWND, path: impl AsRef<OsStr>, reveal: bool) -> io::Result<()> {
    let target = wide(path);
    let arguments = reveal.then(|| {
        wide(format!(
            "/select,\"{}\"",
            String::from_utf16_lossy(&target[..target.len() - 1])
        ))
    });
    let explorer = wide("explorer.exe");
    let result = unsafe {
        ShellExecuteW(
            hwnd,
            wide("open").as_ptr(),
            if reveal {
                explorer.as_ptr()
            } else {
                target.as_ptr()
            },
            arguments
                .as_ref()
                .map_or(ptr::null(), |value| value.as_ptr()),
            ptr::null(),
            SW_SHOWNORMAL,
        )
    };
    if result as isize > 32 {
        Ok(())
    } else {
        Err(io::Error::other("Windows 无法打开该项目"))
    }
}

pub fn pick(hwnd: HWND, mode: &str, default: &str) -> Option<String> {
    let mut buffer = [0_u16; 32768];
    if mode == "folder" {
        let title = wide("选择含 db_storage 的微信账号目录");
        let options = BROWSEINFOW {
            hwndOwner: hwnd,
            lpszTitle: title.as_ptr(),
            ulFlags: (BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE) as u32,
            ..Default::default()
        };
        let item = unsafe { SHBrowseForFolderW(&options) };
        if item.is_null() {
            return None;
        }
        let found = unsafe { SHGetPathFromIDListW(item, buffer.as_mut_ptr()) } != 0;
        unsafe {
            windows_sys::Win32::System::Com::CoTaskMemFree(item.cast());
        }
        return found.then(|| from_buffer(&buffer));
    }
    let save = mode == "save";
    let filter = if save {
        wide("全部文件\0*.*\0")
    } else {
        wide("Google OAuth JSON\0*.json\0")
    };
    let title = wide(if save {
        "另存聊天附件"
    } else {
        "选择 Google OAuth 配置"
    });
    let default = Path::new(default);
    let initial = default.parent().map(wide);
    if save && let Some(name) = default.file_name() {
        for (target, value) in buffer.iter_mut().zip(wide(name)) {
            *target = value;
        }
    }
    let mut options = OPENFILENAMEW {
        lStructSize: size_of::<OPENFILENAMEW>() as u32,
        hwndOwner: hwnd,
        lpstrFilter: filter.as_ptr(),
        lpstrTitle: title.as_ptr(),
        lpstrFile: buffer.as_mut_ptr(),
        nMaxFile: buffer.len() as u32,
        lpstrInitialDir: initial.as_ref().map_or(ptr::null(), |value| value.as_ptr()),
        Flags: OFN_EXPLORER
            | OFN_PATHMUSTEXIST
            | OFN_NOCHANGEDIR
            | if save {
                OFN_OVERWRITEPROMPT
            } else {
                OFN_FILEMUSTEXIST
            },
        ..Default::default()
    };
    let found = unsafe {
        if save {
            GetSaveFileNameW(&mut options)
        } else {
            GetOpenFileNameW(&mut options)
        }
    } != 0;
    found.then(|| from_buffer(&buffer))
}

fn from_buffer(buffer: &[u16]) -> String {
    String::from_utf16_lossy(
        &buffer[..buffer
            .iter()
            .position(|&unit| unit == 0)
            .unwrap_or(buffer.len())],
    )
}

pub fn clipboard(hwnd: HWND, text: &str) -> io::Result<()> {
    let text = wide(text);
    unsafe {
        if OpenClipboard(hwnd) == 0 {
            return Err(io::Error::last_os_error());
        }
        let result = (|| {
            if EmptyClipboard() == 0 {
                return Err(io::Error::last_os_error());
            }
            let memory = GlobalAlloc(GMEM_MOVEABLE, text.len() * 2);
            if memory.is_null() {
                return Err(io::Error::last_os_error());
            }
            let destination = GlobalLock(memory);
            if destination.is_null() {
                GlobalFree(memory);
                return Err(io::Error::last_os_error());
            }
            ptr::copy_nonoverlapping(text.as_ptr(), destination.cast(), text.len());
            GlobalUnlock(memory);
            if SetClipboardData(13, memory).is_null() {
                GlobalFree(memory);
                return Err(io::Error::last_os_error());
            }
            Ok(())
        })();
        CloseClipboard();
        result
    }
}

fn crypt(bytes: &[u8], encrypt: bool) -> io::Result<Vec<u8>> {
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes.len().try_into().map_err(io::Error::other)?,
        pbData: bytes.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    unsafe {
        let result = if encrypt {
            CryptProtectData(
                &input,
                ptr::null(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                ptr::null_mut(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        };
        if result == 0 {
            return Err(io::Error::last_os_error());
        }
        let value = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        LocalFree(output.pbData.cast());
        Ok(value)
    }
}

pub fn decrypt(bytes: &[u8]) -> io::Result<String> {
    let blob = bytes.strip_prefix(b"v10").unwrap_or(bytes);
    String::from_utf8(crypt(blob, false)?).map_err(io::Error::other)
}

pub fn write_vault(file: &Path, value: &str) -> io::Result<()> {
    let mut bytes = b"v10".to_vec();
    bytes.extend(crypt(value.as_bytes(), true)?);
    let temporary = file.with_extension("rust-tmp");
    fs::write(&temporary, bytes)?;
    fs::rename(temporary, file)
}
