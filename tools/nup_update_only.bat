@echo off
rem Writes a copy of a .nup dump of Ocarina of Time that holds only the update (1.1.1), without the base game.
rem The original .nup is only read, never changed. The copy goes next to it, named "... (update only).nup".
rem
rem Use: drag the .nup onto this file, or start it and paste the .nup's path.
rem It needs about as much free space as the original .nup, because the update is almost all of it.
rem Set NUP_STRIP_CHECK=1 to only check the .nup and print what it would write.
rem
rem The work is done by the C# code below the batch lines, which PowerShell compiles on each run.
rem nup_base_only.bat, nup_update_only.bat, nup_update_changes.bat and nup_romfs.bat hold the same code: only these
rem lines differ.
setlocal DisableDelayedExpansion
set "NUP_KEEP=update"
title Keep only the update of a .nup
set "NUP_IN=%~f1"
if "%~1"=="" (
    echo Drag your .nup file onto this batch file, or paste its path here and press Enter.
    set /p "NUP_IN=Path: "
)
if not defined NUP_IN exit /b 1
set "NUP_IN=%NUP_IN:"=%"
set "NUP_BAT=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s = [IO.File]::ReadAllText($env:NUP_BAT); $i = $s.IndexOf('//' + 'CSHARP'); Add-Type -TypeDefinition $s.Substring($i) -Language CSharp; exit [NupStrip]::Run($env:NUP_IN, $env:NUP_KEEP, $env:NUP_STRIP_CHECK -eq '1', -not $env:NUP_NOPAUSE)"
set "RC=%ERRORLEVEL%"
echo.
if not defined NUP_NOPAUSE pause
exit /b %RC%

//CSHARP
// The .nup layout (little endian, nothing encrypted), as tools/nup.py reads it:
//   0x00 superblock, 0x80 bytes: "NUP\x01", u16, u16, u64 toc offset, u64 toc size, sha256(toc), u64 blob offset,
//        u64 file size, u32 package count, u32, 16-byte id, 32 bytes
//   TOC: 0x30 header (u32 magic, u32 packages, u64 dirs, u64 files, u64 name pool offset, u64 pool length, u64),
//        packages 0x40 each, dirs 0x18 each, files 0x50 each, children u32 each, then the name pool.
//   A child below the dir count is a dir, else dir count + file index. File data offsets are absolute, and files with
//   the same bytes share one copy of them.
// Modes (NUP_KEEP):
//   base, update: a copy of the .nup that holds that one package. Each package's dirs, files and children sit in one
//     unbroken run of the TOC: the copy keeps the package and its runs, renumbers them to start at 0, and copies only
//     the data its files use. The name pool is kept whole.
//   changes: a folder with the update's files that the base game does not have with the same bytes at the same path,
//     and changes.txt, which lists the files added, changed and removed.
//   romfs: a folder with one package's RomFS as files. With more than one package it asks which (or NUP_PACKAGE).
// Every file copied is checked against its sha256 in the TOC.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

public static class NupStrip
{
    const int PKG = 0x40, DIR = 0x18, FIL = 0x50, HDR = 0x30;
    const int CHUNK = 8 << 20;

    static uint U32(byte[] b, long o) { return BitConverter.ToUInt32(b, (int)o); }
    static ulong U64(byte[] b, long o) { return BitConverter.ToUInt64(b, (int)o); }
    static void P32(byte[] b, long o, uint v) { Array.Copy(BitConverter.GetBytes(v), 0, b, o, 4); }
    static void P64(byte[] b, long o, ulong v) { Array.Copy(BitConverter.GetBytes(v), 0, b, o, 8); }
    static long Align(long v, long a) { return (v + a - 1) / a * a; }
    static string Gb(long v) { return (v / 1e9).ToString("0.00") + " GB"; }
    static string KindName(int k) { return k == 0 ? "base game" : k == 1 ? "update" : k == 2 ? "DLC" : "kind " + k; }

    class Fail : Exception { public Fail(string m) : base(m) { } }

    // The superblock and the table of contents, read and checked, with where each part of the table starts
    class Toc
    {
        public byte[] Sb, Data;
        public long Npkg, Ndir, Nfile, PoolOff, PoolLen, PkgAt, DirAt, FileAt, ChildAt, Nchild;
        public long Pkg(int i) { return PkgAt + i * PKG; }
        public long File(long fi) { return FileAt + fi * FIL; }
        public int Kind(int i) { return Data[Pkg(i) + 24]; }
        public uint Version(int i) { return U32(Data, Pkg(i) + 20); }
        public ulong Title(int i) { return U64(Data, Pkg(i)); }
    }

    static void ReadFull(Stream s, byte[] b, int off, int n)
    {
        while (n > 0)
        {
            int got = s.Read(b, off, n);
            if (got <= 0) throw new Fail("the .nup ends early: it is incomplete or damaged");
            off += got; n -= got;
        }
    }

    public static int Run(string src, string mode, bool checkOnly, bool ask)
    {
        try
        {
            if (mode == "changes") return Changes(src, checkOnly, ask);
            if (mode == "romfs") return Romfs(src, checkOnly, ask);
            int kind = mode == "base" ? 0 : mode == "update" ? 1 : -1;
            if (kind < 0) throw new Fail("NUP_KEEP must be base, update, changes or romfs, not " + mode);
            return Keep(src, kind, checkOnly, ask);
        }
        catch (Fail e) { Console.WriteLine(); Console.WriteLine("ERROR: " + e.Message); }
        catch (Exception e) { Console.WriteLine(); Console.WriteLine("ERROR: " + e.GetType().Name + ": " + e.Message); }
        return 1;
    }

    static Toc Load(string src)
    {
        if (!File.Exists(src)) throw new Fail("file not found: " + src);
        Console.WriteLine("Reading " + src);
        var t = new Toc();
        t.Sb = new byte[0x80];
        using (var f = new FileStream(src, FileMode.Open, FileAccess.Read, FileShare.Read))
        {
            if (f.Length < 0x80) throw new Fail("not a .nup file (too short)");
            ReadFull(f, t.Sb, 0, 0x80);
            if (Encoding.ASCII.GetString(t.Sb, 0, 4) != "NUP\u0001") throw new Fail("not a .nup file");
            ulong tocOff = U64(t.Sb, 8), tocSize = U64(t.Sb, 16);
            if (tocSize > int.MaxValue || tocOff > (ulong)f.Length || tocSize > (ulong)f.Length - tocOff)
                throw new Fail("the table of contents is damaged");
            t.Data = new byte[tocSize];
            f.Position = (long)tocOff;
            ReadFull(f, t.Data, 0, t.Data.Length);
        }
        using (var h = SHA256.Create())
        {
            byte[] d = h.ComputeHash(t.Data);
            for (int i = 0; i < 32; i++)
                if (d[i] != t.Sb[24 + i]) throw new Fail("the table of contents is damaged (its sha256 does not match)");
        }
        byte[] toc = t.Data;
        if (U32(toc, 0) != 0x01434F54) throw new Fail("unknown table of contents");
        t.Npkg = U32(toc, 4); t.Ndir = (long)U64(toc, 8); t.Nfile = (long)U64(toc, 16);
        t.PoolOff = (long)U64(toc, 24); t.PoolLen = (long)U64(toc, 32);
        t.PkgAt = HDR; t.DirAt = t.PkgAt + t.Npkg * PKG; t.FileAt = t.DirAt + t.Ndir * DIR; t.ChildAt = t.FileAt + t.Nfile * FIL;
        if (t.ChildAt > t.PoolOff || t.PoolOff + t.PoolLen > toc.Length) throw new Fail("the table of contents is malformed");
        t.Nchild = (t.PoolOff - t.ChildAt) / 4;
        for (int i = 0; i < t.Npkg; i++)
            Console.WriteLine("  package " + i + ": " + t.Title(i).ToString("X16") + " v" + t.Version(i) + " (" + KindName(t.Kind(i)) + ")");
        return t;
    }

    // The one package of this kind; why there is none, in words, when it is missing
    static int Find(Toc t, int kind)
    {
        int found = -1;
        for (int i = 0; i < t.Npkg; i++)
            if (t.Kind(i) == kind)
            {
                if (found >= 0) throw new Fail("this .nup holds more than one " + KindName(kind));
                found = i;
            }
        if (found < 0)
        {
            if (t.Npkg == 1 && kind == 0)
                throw new Fail("this .nup holds no separate base game. Its one package is the game with the update already in it, so there is no base game to work with.");
            throw new Fail("this .nup holds no " + KindName(kind));
        }
        return found;
    }

    // Path below the dir -> file index, for every file below it (a package's root dir is its offset 8, romfs 16)
    static Dictionary<string, long> Files(Toc t, long dir)
    {
        byte[] toc = t.Data;
        var result = new Dictionary<string, long>(StringComparer.Ordinal);
        var seen = new HashSet<long>();
        var stack = new Stack<KeyValuePair<long, string>>();
        stack.Push(new KeyValuePair<long, string>(dir, ""));
        while (stack.Count > 0)
        {
            var top = stack.Pop();
            long d = top.Key;
            if (d >= t.Ndir || !seen.Add(d)) throw new Fail("the table of contents is malformed (dir " + d + ")");
            long first = U32(toc, t.DirAt + d * DIR + 8), count = U32(toc, t.DirAt + d * DIR + 12);
            if (first + count > t.Nchild) throw new Fail("the table of contents is malformed (children of dir " + d + ")");
            for (long c = first; c < first + count; c++)
            {
                long v = U32(toc, t.ChildAt + c * 4);
                if (v < t.Ndir)
                    stack.Push(new KeyValuePair<long, string>(v, top.Value + Name(t, U32(toc, t.DirAt + v * DIR), U32(toc, t.DirAt + v * DIR + 4)) + "/"));
                else if (v - t.Ndir < t.Nfile)
                {
                    long fi = v - t.Ndir;
                    result[top.Value + Name(t, U32(toc, t.File(fi)), U32(toc, t.File(fi) + 4))] = fi;
                }
                else throw new Fail("the table of contents is malformed (child " + c + ")");
            }
        }
        return result;
    }

    static string Name(Toc t, long off, long len)
    {
        if (off + len > t.PoolLen) throw new Fail("the table of contents is malformed (a name lies outside the name pool)");
        string s = Encoding.UTF8.GetString(t.Data, (int)(t.PoolOff + off), (int)len);
        if (s.Length == 0 || s == "." || s == ".." || s.IndexOfAny(new char[] { '/', '\\', ':', '\0' }) >= 0)
            throw new Fail("the .nup holds a file or folder name this tool will not write: " + s);
        return s;
    }

    static bool SameData(Toc t, long a, long b)
    {
        byte[] toc = t.Data;
        long ea = t.File(a), eb = t.File(b);
        if (U64(toc, ea + 8) != U64(toc, eb + 8)) return false;
        for (int i = 0; i < 32; i++) if (toc[ea + 32 + i] != toc[eb + 32 + i]) return false;
        return true;
    }

    // Copies the stored bytes of a file to out, checking its sha256 when it is stored as is
    static void CopyData(Toc t, FileStream inp, Stream outp, long fi, SHA256 h, byte[] buf, ref long done, long total)
    {
        byte[] toc = t.Data;
        long e = t.File(fi);
        long off = (long)U64(toc, e + 16), stored = (long)U64(toc, e + 24);
        bool check = toc[e + 64] == 0;
        if (inp.Position != off) inp.Position = off;
        h.Initialize();
        long left = stored;
        while (left > 0)
        {
            int n = (int)Math.Min(CHUNK, left);
            ReadFull(inp, buf, 0, n);
            if (check) h.TransformBlock(buf, 0, n, null, 0);
            outp.Write(buf, 0, n);
            left -= n;
            done += n;
            Progress(done, total);
        }
        if (check)
        {
            h.TransformFinalBlock(buf, 0, 0);
            byte[] got = h.Hash;
            for (int i = 0; i < 32; i++)
                if (got[i] != toc[e + 32 + i] || (long)U64(toc, e + 8) != stored)
                    throw new Fail("file " + fi + " in the .nup is damaged (its sha256 does not match), so nothing was kept.");
        }
    }

    static long lastPct = -1;
    static void Progress(long done, long total)
    {
        long pct = total == 0 ? 100 : done * 100 / total;
        if (pct == lastPct) return;
        lastPct = pct;
        Console.Write("\r  copying: " + pct + "%  (" + Gb(done) + " of " + Gb(total) + ")   ");
    }

    static string Stem(string src)
    {
        return Regex.Replace(Path.GetFileNameWithoutExtension(src), @"\s*\[v\d+\].*$", "");
    }

    static long Free(string path, long need)
    {
        string root = Path.GetPathRoot(path);
        long free = new DriveInfo(root).AvailableFreeSpace;
        Console.WriteLine("  free space on " + root + " " + Gb(free));
        return free;
    }

    static void Confirm(bool ask)
    {
        if (!ask) return;
        Console.WriteLine();
        Console.Write("Press Enter to start, or close this window to cancel. ");
        Console.ReadLine();
    }

    // ---- base, update: a .nup with one package ----

    static int Keep(string src, int keepKind, bool checkOnly, bool ask)
    {
        src = Path.GetFullPath(src.Trim());
        Toc t = Load(src);
        byte[] sb = t.Sb, toc = t.Data;
        long ndir = t.Ndir, nfile = t.Nfile, poolOff = t.PoolOff, poolLen = t.PoolLen;
        long dirAt = t.DirAt, fileAt = t.FileAt, childAt = t.ChildAt, nchild = t.Nchild;

        int keep = Find(t, keepKind);
        if (t.Npkg == 1) throw new Fail("this .nup already holds only the " + KindName(keepKind));
        long kp = t.Pkg(keep);
        ulong keepTitle = t.Title(keep);
        uint keepVer = t.Version(keep);

        // Every dir and file below the package's root, and the run of children they use
        var dirs = new HashSet<long>();
        var fileSet = new HashSet<long>();
        long cLo = long.MaxValue, cHi = 0;
        var stack = new Stack<long>();
        stack.Push(U32(toc, kp + 8));
        while (stack.Count > 0)
        {
            long d = stack.Pop();
            if (d >= ndir || !dirs.Add(d)) throw new Fail("the table of contents is malformed (dir " + d + ")");
            long first = U32(toc, dirAt + d * DIR + 8), count = U32(toc, dirAt + d * DIR + 12);
            if (first + count > nchild) throw new Fail("the table of contents is malformed (children of dir " + d + ")");
            if (count > 0) { cLo = Math.Min(cLo, first); cHi = Math.Max(cHi, first + count); }
            for (long c = first; c < first + count; c++)
            {
                long v = U32(toc, childAt + c * 4);
                if (v < ndir) stack.Push(v);
                else if (v - ndir < nfile) { if (!fileSet.Add(v - ndir)) throw new Fail("the table of contents is malformed (a file is listed twice)"); }
                else throw new Fail("the table of contents is malformed (child " + c + ")");
            }
        }
        if (fileSet.Count == 0) throw new Fail("the " + KindName(keepKind) + " holds no files");
        long dLo = long.MaxValue, dHi = 0, fLo = long.MaxValue, fHi = 0;
        foreach (long d in dirs) { dLo = Math.Min(dLo, d); dHi = Math.Max(dHi, d); }
        foreach (long fi in fileSet) { fLo = Math.Min(fLo, fi); fHi = Math.Max(fHi, fi); }
        long D = dirs.Count, F = fileSet.Count, C = cHi - cLo;
        const string mixed = " are mixed in with another package's in the .nup, which this tool cannot handle";
        if (dHi - dLo + 1 != D) throw new Fail("the " + KindName(keepKind) + "'s dirs" + mixed);
        if (fHi - fLo + 1 != F) throw new Fail("the " + KindName(keepKind) + "'s files" + mixed);
        for (long c = cLo; c < cHi; c++)
        {
            long v = U32(toc, childAt + c * 4);
            bool ours = v < ndir ? dirs.Contains(v) : fileSet.Contains(v - ndir);
            if (!ours) throw new Fail("the " + KindName(keepKind) + "'s directory lists" + mixed);
        }

        // The data the package uses: each stored range once, in the order it sits in the .nup
        var ranges = new SortedDictionary<long, long>();   // old offset -> a file index whose data lies there
        for (long fi = fLo; fi <= fHi; fi++)
        {
            long e = fileAt + fi * FIL;
            long off = (long)U64(toc, e + 16), stored = (long)U64(toc, e + 24);
            if (toc[e + 64] > 1) throw new Fail("file " + fi + " uses an unknown codec");
            long had;
            if (ranges.TryGetValue(off, out had))
            {
                if ((long)U64(toc, fileAt + had * FIL + 24) != stored) throw new Fail("two files share data of different sizes at " + off);
            }
            else ranges[off] = fi;
        }
        long metaOff = (long)U64(toc, kp + 32), metaLen = U32(toc, kp + 40);

        // The new layout: superblock, table of contents, the package's meta, then the data
        long nAt = HDR + PKG + D * DIR + F * FIL + C * 4;
        long nPoolOff = Align(nAt, 16);
        long nTocSize = Align(nPoolOff + poolLen, 16);
        long nMetaOff = Align(0x80 + nTocSize, 16);
        long nBlob = Align(nMetaOff + metaLen, 64);
        var newOff = new Dictionary<long, long>();
        long cur = nBlob, end = nBlob, dataBytes = 0;
        foreach (var r in ranges)
        {
            long stored = (long)U64(toc, fileAt + r.Value * FIL + 24);
            newOff[r.Key] = cur;
            end = cur + stored;
            cur = Align(end, 16);
            dataBytes += stored;
        }
        long total = end;

        byte[] nt = new byte[nTocSize];
        Array.Copy(toc, 0, nt, 0, HDR);
        P32(nt, 4, 1);
        P64(nt, 8, (ulong)D);
        P64(nt, 16, (ulong)F);
        P64(nt, 24, (ulong)nPoolOff);
        Array.Copy(toc, kp, nt, HDR, PKG);
        for (int k = 8; k <= 16; k += 4)   // root, exefs and romfs dirs; one past the dirs means the package has none
        {
            long v = U32(toc, kp + k);
            if (v < ndir && !dirs.Contains(v)) throw new Fail("the package names a dir outside itself");
            P32(nt, HDR + k, v < ndir ? (uint)(v - dLo) : 0xFFFFFFFFu);
        }
        P64(nt, HDR + 32, (ulong)nMetaOff);
        long nDirAt = HDR + PKG, nFileAt = nDirAt + D * DIR, nChildAt = nFileAt + F * FIL;
        Array.Copy(toc, dirAt + dLo * DIR, nt, nDirAt, D * DIR);
        for (long d = 0; d < D; d++)
        {
            long e = nDirAt + d * DIR;
            long first = U32(nt, e + 8);
            P32(nt, e + 8, U32(nt, e + 12) > 0 ? (uint)(first - cLo) : 0);
        }
        Array.Copy(toc, fileAt + fLo * FIL, nt, nFileAt, F * FIL);
        for (long fi = 0; fi < F; fi++)
        {
            long e = nFileAt + fi * FIL;
            P64(nt, e + 16, (ulong)newOff[(long)U64(nt, e + 16)]);
        }
        for (long c = 0; c < C; c++)
        {
            long v = U32(toc, childAt + (cLo + c) * 4);
            P32(nt, nChildAt + c * 4, (uint)(v < ndir ? v - dLo : v - ndir - fLo + D));
        }
        Array.Copy(toc, poolOff, nt, nPoolOff, poolLen);

        string stem = Stem(src);
        var tid = new Regex(@"\[[0-9A-Fa-f]{16}\]");
        stem = tid.IsMatch(stem) ? tid.Replace(stem, "[" + keepTitle.ToString("X16") + "]", 1) : stem + " [" + keepTitle.ToString("X16") + "]";
        string label = keepKind == 0 ? "base game only" : "update only";
        string dst = Path.Combine(Path.GetDirectoryName(src), stem + "[v" + keepVer + "] (" + label + ").nup");
        string part = dst + ".partial";

        Console.WriteLine();
        Console.WriteLine("Keeps the " + KindName(keepKind) + " (v" + keepVer + "): " + F + " files, " + Gb(dataBytes) + " of data.");
        Console.WriteLine("Writes " + dst);
        Console.WriteLine("  size " + Gb(total));
        long free = Free(dst, total);
        if (checkOnly) { Console.WriteLine(); Console.WriteLine("Check only: nothing written."); return 0; }
        if (File.Exists(dst)) throw new Fail("the copy already exists. Delete or rename it first: " + dst);
        if (free < total + (256L << 20)) throw new Fail("not enough free space on " + Path.GetPathRoot(dst) + " (needs " + Gb(total) + ")");
        Confirm(ask);

        byte[] sha;
        using (var h = SHA256.Create()) sha = h.ComputeHash(nt);
        byte[] nsb = (byte[])sb.Clone();
        P64(nsb, 8, 0x80);
        P64(nsb, 16, (ulong)nTocSize);
        Array.Copy(sha, 0, nsb, 24, 32);
        P64(nsb, 56, (ulong)nBlob);
        P64(nsb, 64, (ulong)total);
        P32(nsb, 72, 1);

        var started = DateTime.Now;
        try
        {
            using (var inp = new FileStream(src, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16, FileOptions.SequentialScan))
            using (var outp = new FileStream(part, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 16))
            using (var h = SHA256.Create())
            {
                outp.Write(new byte[0x80], 0, 0x80);   // the superblock goes in last, once everything else is written
                outp.Write(nt, 0, nt.Length);
                byte[] meta = new byte[metaLen];
                inp.Position = metaOff;
                ReadFull(inp, meta, 0, (int)metaLen);
                outp.Position = nMetaOff;
                outp.Write(meta, 0, meta.Length);
                byte[] buf = new byte[CHUNK];
                long done = 0;
                foreach (var r in ranges)
                {
                    outp.Position = newOff[r.Key];
                    CopyData(t, inp, outp, r.Value, h, buf, ref done, dataBytes);
                }
                outp.SetLength(total);
                outp.Position = 0;
                outp.Write(nsb, 0, nsb.Length);
                outp.Flush(true);
            }
        }
        catch { try { File.Delete(part); } catch { } throw; }
        File.Move(part, dst);
        Console.WriteLine();
        Console.WriteLine();
        Console.WriteLine("Done in " + (int)(DateTime.Now - started).TotalMinutes + " min. The " + label + " copy is:");
        Console.WriteLine("  " + dst);
        return 0;
    }

    // ---- changes: the update's files that differ from the base game's, into a folder ----

    static int Changes(string src, bool checkOnly, bool ask)
    {
        src = Path.GetFullPath(src.Trim());
        Toc t = Load(src);
        int bi = Find(t, 0), ui = Find(t, 1);
        var baseFiles = Files(t, U32(t.Data, t.Pkg(bi) + 8));
        var updFiles = Files(t, U32(t.Data, t.Pkg(ui) + 8));

        var added = new List<string>();
        var changed = new List<string>();
        var removed = new List<string>();
        foreach (var kv in updFiles)
        {
            long b;
            if (!baseFiles.TryGetValue(kv.Key, out b)) added.Add(kv.Key);
            else if (!SameData(t, b, kv.Value)) changed.Add(kv.Key);
        }
        foreach (var k in baseFiles.Keys) if (!updFiles.ContainsKey(k)) removed.Add(k);
        added.Sort(StringComparer.Ordinal); changed.Sort(StringComparer.Ordinal); removed.Sort(StringComparer.Ordinal);

        var copy = added.Concat(changed).ToList();
        long bytes = 0;
        foreach (var p in copy)
        {
            long e = t.File(updFiles[p]);
            if (t.Data[e + 64] != 0) throw new Fail("the update's file " + p + " is compressed, which this tool cannot unpack");
            bytes += (long)U64(t.Data, e + 8);
        }

        string dst = Path.Combine(Path.GetDirectoryName(src),
            Stem(src) + " [changes v" + t.Version(bi) + " to v" + t.Version(ui) + "]");
        string part = dst + ".partial";

        Console.WriteLine();
        Console.WriteLine("Compared with the base game (v" + t.Version(bi) + "), the update (v" + t.Version(ui) + "):");
        Console.WriteLine("  adds " + added.Count + " files, changes " + changed.Count + ", removes " + removed.Count);
        Console.WriteLine("Copies the " + copy.Count + " added and changed files (" + Gb(bytes) + ") and a list of the changes to");
        Console.WriteLine("  " + dst);
        long free = Free(dst, bytes);
        if (checkOnly) { Console.WriteLine(); Console.WriteLine("Check only: nothing written."); return 0; }
        StartFolder(dst, part, free, bytes);
        Confirm(ask);

        var started = DateTime.Now;
        try
        {
            ExtractTo(t, src, part, copy.Select(p => new KeyValuePair<string, long>(p, updFiles[p])), bytes);
            var list = new StringBuilder();
            list.AppendLine("The update (v" + t.Version(ui) + ", " + t.Title(ui).ToString("X16") + ") compared with the base game (v"
                            + t.Version(bi) + ", " + t.Title(bi).ToString("X16") + ").");
            list.AppendLine("This folder holds the added and changed files, as they are in the update. Sizes are in bytes.");
            list.AppendLine();
            Section(list, "Added", added, t, updFiles);
            Section(list, "Changed", changed, t, updFiles);
            Section(list, "Removed (in the base game, not in the update)", removed, t, baseFiles);
            File.WriteAllText(Long(Path.Combine(part, "changes.txt")), list.ToString(), new UTF8Encoding(false));
        }
        catch { try { Directory.Delete(Long(part), true); } catch { } throw; }
        Directory.Move(Long(part), Long(dst));
        Console.WriteLine();
        Console.WriteLine();
        Console.WriteLine("Done in " + (int)(DateTime.Now - started).TotalMinutes + " min. The changed files are in:");
        Console.WriteLine("  " + dst);
        return 0;
    }

    // ---- romfs: one package's RomFS as files and folders ----

    static int Romfs(string src, bool checkOnly, bool ask)
    {
        src = Path.GetFullPath(src.Trim());
        Toc t = Load(src);
        int pkg = -1;
        string pick = Environment.GetEnvironmentVariable("NUP_PACKAGE");
        if (t.Npkg == 1) pkg = 0;
        else if (!string.IsNullOrEmpty(pick)) int.TryParse(pick, out pkg);
        else if (ask)
        {
            Console.WriteLine();
            Console.Write("Which package's RomFS? Type its number and press Enter: ");
            int.TryParse((Console.ReadLine() ?? "").Trim(), out pkg);
        }
        else throw new Fail("this .nup holds " + t.Npkg + " packages: set NUP_PACKAGE to the number of the one to use");
        if (pkg < 0 || pkg >= t.Npkg) throw new Fail("there is no package with that number");

        long romfsDir = U32(t.Data, t.Pkg(pkg) + 16);
        if (romfsDir >= t.Ndir) throw new Fail("package " + pkg + " holds no RomFS");
        var files = Files(t, romfsDir);
        long bytes = 0;
        foreach (var kv in files)
        {
            long e = t.File(kv.Value);
            if (t.Data[e + 64] != 0) throw new Fail("the RomFS file " + kv.Key + " is compressed, which this tool cannot unpack");
            bytes += (long)U64(t.Data, e + 8);
        }

        string stem = Stem(src);
        var tid = new Regex(@"\[[0-9A-Fa-f]{16}\]");
        string title = "[" + t.Title(pkg).ToString("X16") + "]";
        stem = tid.IsMatch(stem) ? tid.Replace(stem, title, 1) : stem + " " + title;
        string dst = Path.Combine(Path.GetDirectoryName(src), stem + "[v" + t.Version(pkg) + "] romfs");
        string part = dst + ".partial";

        Console.WriteLine();
        Console.WriteLine("Copies the RomFS of package " + pkg + " (" + KindName(t.Kind(pkg)) + ", v" + t.Version(pkg) + "): "
                          + files.Count + " files, " + Gb(bytes) + ", to");
        Console.WriteLine("  " + dst);
        long free = Free(dst, bytes);
        if (checkOnly) { Console.WriteLine(); Console.WriteLine("Check only: nothing written."); return 0; }
        StartFolder(dst, part, free, bytes);
        Confirm(ask);

        var started = DateTime.Now;
        try { ExtractTo(t, src, part, files, bytes); }
        catch { try { Directory.Delete(Long(part), true); } catch { } throw; }
        Directory.Move(Long(part), Long(dst));
        Console.WriteLine();
        Console.WriteLine();
        Console.WriteLine("Done in " + (int)(DateTime.Now - started).TotalMinutes + " min. The RomFS is in:");
        Console.WriteLine("  " + dst);
        return 0;
    }

    // ---- writing files into a folder ----

    // The path in the form Windows accepts past 260 characters, whatever its long-path setting
    static string Long(string path)
    {
        if (path.StartsWith(@"\\?\")) return path;
        if (path.StartsWith(@"\\")) return @"\\?\UNC\" + path.Substring(2);
        return @"\\?\" + path;
    }

    static void StartFolder(string dst, string part, long free, long bytes)
    {
        if (Directory.Exists(Long(dst)) || File.Exists(Long(dst))) throw new Fail("the folder already exists. Delete or rename it first: " + dst);
        if (Directory.Exists(Long(part))) Directory.Delete(Long(part), true);   // left by a run that was closed part way
        if (free < bytes + (256L << 20)) throw new Fail("not enough free space on " + Path.GetPathRoot(dst) + " (needs " + Gb(bytes) + ")");
    }

    // Writes each (path below the folder, file index) into the folder, in the order the data sits in the .nup so the
    // reads run forward through it
    static void ExtractTo(Toc t, string src, string folder, IEnumerable<KeyValuePair<string, long>> files, long bytes)
    {
        Directory.CreateDirectory(Long(folder));
        var order = files.OrderBy(kv => U64(t.Data, t.File(kv.Value) + 16)).ToList();
        var made = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        using (var inp = new FileStream(src, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 16, FileOptions.SequentialScan))
        using (var h = SHA256.Create())
        {
            byte[] buf = new byte[CHUNK];
            long done = 0;
            foreach (var kv in order)
            {
                string path = Long(Path.Combine(folder, kv.Key.Replace('/', Path.DirectorySeparatorChar)));
                string dir = Path.GetDirectoryName(path);
                if (made.Add(dir)) Directory.CreateDirectory(dir);
                using (var outp = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None, 1 << 16))
                    CopyData(t, inp, outp, kv.Value, h, buf, ref done, bytes);
            }
        }
    }

    static void Section(StringBuilder sb, string title, List<string> paths, Toc t, Dictionary<string, long> files)
    {
        sb.AppendLine(title + ": " + paths.Count);
        foreach (var p in paths) sb.AppendLine("  " + U64(t.Data, t.File(files[p]) + 8).ToString().PadLeft(12) + "  " + p);
        sb.AppendLine();
    }
}
