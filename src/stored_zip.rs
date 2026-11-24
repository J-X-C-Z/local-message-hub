//! Internal bounded ZIP32/STORED emitter for generated media and fixed XLSX members.
const XLSX_MEMBERS: [&str; 5] = [
    "[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
    "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml",
];

#[derive(Clone,Copy)]
enum Names {
    #[cfg(target_os = "linux")]
    Flat(usize),
    Xlsx,
}

pub(crate) struct StoredZip {
    bytes: Vec<u8>,
    central: Vec<ZipEntry>,
    limit: usize,
    names: Names,
}

#[derive(Debug)]
pub(crate) struct Limit;

struct ZipEntry { name:String, offset:u32, size:u32, crc:u32 }
fn word(out:&mut Vec<u8>,n:u16) {out.extend(n.to_le_bytes());}
fn dword(out:&mut Vec<u8>,n:u32) {out.extend(n.to_le_bytes());}
impl StoredZip {
    #[cfg(target_os = "linux")]
    pub(crate) fn flat(capacity:usize,limit:usize,entries:usize)->Self {
        Self{bytes:Vec::with_capacity(capacity.min(limit)),central:Vec::new(),limit,names:Names::Flat(entries)}
    }

    pub(crate) fn xlsx(limit:usize)->Self {
        Self{bytes:Vec::new(),central:Vec::with_capacity(XLSX_MEMBERS.len()),limit,names:Names::Xlsx}
    }
    pub(crate) fn push(&mut self,name:&str,bytes:&[u8])->Result<(),Limit> {
        let allowed=match self.names {
            #[cfg(target_os = "linux")]
            Names::Flat(max) => self.central.len()<max && (1..=64).contains(&name.len())
                && name.bytes().all(|b|b.is_ascii_alphanumeric()||matches!(b,b'.'|b'-')),
            Names::Xlsx => XLSX_MEMBERS.get(self.central.len()).copied()==Some(name),
        };
        if !allowed
            || self.bytes.len().saturating_add(30+name.len()).saturating_add(bytes.len())>self.limit {
            return Err(Limit);
        }
        let offset=u32::try_from(self.bytes.len()).map_err(|_|Limit)?;
        let size=u32::try_from(bytes.len()).map_err(|_|Limit)?;
        let crc=crc32fast::hash(bytes);
        let out=&mut self.bytes;
        dword(out,0x04034b50);
        for n in [20,0,0,0,33] {word(out,n);} // version, flags, STORED, time, 1980-01-01
        for n in [crc,size,size] {dword(out,n);}
        word(out,name.len() as u16);word(out,0);
        out.extend(name.as_bytes());out.extend(bytes);
        self.central.push(ZipEntry{name:name.into(),offset,size,crc});
        Ok(())
    }
    // Scratch payload is private until fill succeeds. None is an admission
    // refusal before calling fill; the caller may then use the existing path.
    #[cfg(target_os = "linux")]
    pub(crate) fn push_flat_direct<T,E>(&mut self,name:&str,size:usize,
        fill:impl FnOnce(&mut [u8])->Result<T,E>)->Option<Result<T,E>> {
        let Names::Flat(max)=self.names else {return None;};
        if self.central.len()>=max || !(1..=64).contains(&name.len())
            || !name.bytes().all(|b|b.is_ascii_alphanumeric()||matches!(b,b'.'|b'-'))
        {return None;}
        let old=self.bytes.len();
        let offset=u32::try_from(old).ok()?;
        let size_field=u32::try_from(size).ok()?;
        let payload=old.checked_add(30+name.len())?;
        let end=payload.checked_add(size)?;
        if end>self.limit || end>self.bytes.capacity() {return None;}
        self.bytes.resize(end,0);
        let result=match fill(&mut self.bytes[payload..end]) {
            Ok(value)=>value,
            Err(error)=>{
                self.bytes.truncate(old);
                return Some(Err(error));
            }
        };
        let crc=crc32fast::hash(&self.bytes[payload..end]);
        let header=&mut self.bytes[old..payload];
        header[0..4].copy_from_slice(&0x04034b50u32.to_le_bytes());
        header[4..6].copy_from_slice(&20u16.to_le_bytes());
        header[12..14].copy_from_slice(&33u16.to_le_bytes());
        header[14..18].copy_from_slice(&crc.to_le_bytes());
        header[18..22].copy_from_slice(&size_field.to_le_bytes());
        header[22..26].copy_from_slice(&size_field.to_le_bytes());
        header[26..28].copy_from_slice(&(name.len() as u16).to_le_bytes());
        header[30..].copy_from_slice(name.as_bytes());
        self.central.push(ZipEntry{name:name.into(),offset,size:size_field,crc});
        Some(Ok(result))
    }

    pub(crate) fn finish(mut self)->Result<Vec<u8>,Limit> {
        if matches!(self.names,Names::Xlsx) && self.central.len()!=XLSX_MEMBERS.len() { return Err(Limit); }
        let final_size=self.central.iter().fold(self.bytes.len().saturating_add(22),|len,entry|
            len.saturating_add(46+entry.name.len()));
        if final_size>self.limit {return Err(Limit);}
        let central_start=u32::try_from(self.bytes.len()).map_err(|_|Limit)?;
        let total=self.central.len() as u16;
        let out=&mut self.bytes;
        for entry in self.central {
            dword(out,0x02014b50);
            for n in [20,20,0,0,0,33] {word(out,n);}
            for n in [entry.crc,entry.size,entry.size] {dword(out,n);}
            for n in [entry.name.len() as u16,0,0,0,0] {word(out,n);}
            dword(out,0);dword(out,entry.offset);
            out.extend(entry.name.as_bytes());
        }
        let central_size=u32::try_from(out.len()).map_err(|_|Limit)?-central_start;
        dword(out,0x06054b50);
        for n in [0,0,total,total] {word(out,n);}
        dword(out,central_size);dword(out,central_start);word(out,0);
        if out.len()>self.limit {return Err(Limit);}
        Ok(self.bytes)
    }
}

#[cfg(all(test,target_os="linux"))]
mod direct_tests {
    use super::*;

    #[test]
    fn flat_member_direct_matches_old_zip_and_discard_does_not_commit() {
        let data:Vec<u8>=(0..16384).map(|n|(n*17) as u8).collect();
        let mut expected=StoredZip::flat(32768,32768,3);
        expected.push("attachment-01.bin",&data).unwrap();
        expected.push("attachment-02.bin",b"").unwrap();
        let expected=expected.finish().unwrap();
        let mut actual=StoredZip::flat(32768,32768,3);
        let original_capacity=actual.bytes.capacity();
        let original_ptr=actual.bytes.as_ptr();
        assert_eq!(actual.push_flat_direct("attachment-01.bin",data.len(),|buf|{
            buf.copy_from_slice(&data);
            Ok::<_,()>(buf.len())
        }),Some(Ok(data.len())));
        let saved=actual.bytes.len();
        assert_eq!(actual.push_flat_direct("never-committed.bin",17,|buf|{
            buf.fill(0x9f);
            Err::<(),_>("test failure")
        }),Some(Err("test failure")));
        assert_eq!(actual.bytes.len(),saved);
        assert_eq!(actual.central.len(),1);
        assert_eq!(actual.bytes.capacity(),original_capacity);
        assert_eq!(actual.bytes.as_ptr(),original_ptr);
        let not_called=|_:&mut [u8]| -> Result<(),()> {panic!("inadmissible fill")};
        assert_eq!(actual.push_flat_direct("../x",4,not_called),None);
        assert_eq!(actual.push_flat_direct("over.bin",32768,not_called),None);
        assert_eq!(StoredZip::flat(32,32768,1).push_flat_direct("fit.bin",32,not_called),None);
        assert_eq!(actual.push_flat_direct("attachment-02.bin",0,|buf|{
            assert!(buf.is_empty());
            Ok::<_,()>(())
        }),Some(Ok(())));
        assert_eq!(actual.finish().unwrap(),expected);
    }
}
