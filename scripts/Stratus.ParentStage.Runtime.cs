// Windows-only, dependency-free primitives for the fail-closed parent-stage lease.
// This file must be held read-only and hash-bound before Add-Type is called.
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using Microsoft.Win32.SafeHandles;

namespace Stratus.ParentStage {
    public static class Files {
        public static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFile(string name, uint access, uint share,
            IntPtr security, uint creation, uint flags, IntPtr template);
        [StructLayout(LayoutKind.Sequential)] private struct FileTimeParts {
            public uint Low, High;
        }
        [StructLayout(LayoutKind.Sequential)] private struct FileInformation {
            public uint Attributes;
            public FileTimeParts Created, Accessed, Written;
            public uint VolumeSerialNumber, SizeHigh, SizeLow, NumberOfLinks, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", SetLastError = true)]
        private static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
        // Rejection-only metadata check on an already opened, caller-owned file.
        // It does not open, close, mutate, or grant provenance to any handle.
        // The caller must keep its exact no-write/no-delete-sharing handle held.
        public static void RequireSingleLink(FileStream stream) {
            if (stream == null) throw new ArgumentNullException("stream");
            lock (stream) {
                if (!stream.CanRead || stream.CanWrite || !stream.CanSeek ||
                    stream.SafeFileHandle.IsClosed || stream.SafeFileHandle.IsInvalid)
                    throw new InvalidDataException("Single-link evidence requires a live read-only file handle.");
                FileInformation information;
                if (!GetFileInformationByHandle(stream.SafeFileHandle, out information))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Read held evidence file information");
                if (information.NumberOfLinks != 1 ||
                    (information.Attributes & ((uint)FileAttributes.Directory | (uint)FileAttributes.ReparsePoint)) != 0)
                    throw new InvalidDataException("Held evidence must be a regular, non-reparse file with exactly one link.");
            }
        }
        // Rejection-only access probes: OPEN_EXISTING never mutates the file, but
        // even three denied probes do NOT establish exclusive creation provenance.
        // Composite existing holders can deny every new probe while one can write.
        public static void AssertExclusiveFence(FileStream stream) {
            if (stream == null) throw new ArgumentNullException("stream");
            lock (stream) {
                if (!stream.CanRead || stream.SafeFileHandle.IsClosed || stream.SafeFileHandle.IsInvalid)
                    throw new InvalidDataException("Borrowed fence is not a live readable handle.");
                RegularAncestry(stream.Name, true);
                foreach (uint access in new uint[] { 0x80000000, 0x40000000, 0x00010000 }) {
                    using (SafeFileHandle probe = CreateFile(stream.Name, access, 0x00000007,
                            IntPtr.Zero, 3, 0, IntPtr.Zero)) {
                        int error = Marshal.GetLastWin32Error();
                        if (!probe.IsInvalid)
                            throw new InvalidDataException("Borrowed fence permits read, write or delete access.");
                        if (error != 32)
                            throw new Win32Exception(error, "Exclusive fence sharing could not be proved.");
                    }
                }
            }
        }
        public static string Hash(byte[] bytes) {
            using (SHA256 hash = SHA256.Create()) {
                return BitConverter.ToString(hash.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
            }
        }
        public static string HashStream(FileStream stream) {
            lock (stream) {
                stream.Position = 0;
                try {
                    using (SHA256 hash = SHA256.Create()) {
                        return BitConverter.ToString(hash.ComputeHash(stream)).Replace("-", "").ToLowerInvariant();
                    }
                } finally { stream.Position = 0; }
            }
        }
        public static void RegularAncestry(string path, bool mustExist) {
            string full = Path.GetFullPath(path);
            if (!String.Equals(path, full, StringComparison.Ordinal))
                throw new InvalidDataException("A canonical absolute path is required.");
            string item = full;
            bool leaf = true;
            while (!String.IsNullOrEmpty(item)) {
                bool exists = File.Exists(item) || Directory.Exists(item);
                if (!exists && (!leaf || mustExist))
                    throw new FileNotFoundException("Missing path component.", item);
                if (exists && (File.GetAttributes(item) & FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("Reparse ancestry is forbidden: " + item);
                item = Path.GetDirectoryName(item);
                leaf = false;
            }
        }
        public static FileStream Lock(string path, string expectedHash) {
            RegularAncestry(path, true);
            FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
            try {
                if (HashStream(stream) != expectedHash)
                    throw new InvalidDataException("Held file hash differs: " + path);
                return stream;
            } catch (Exception primary) {
                try { stream.Dispose(); }
                catch (Exception cleanup) { throw new AggregateException(primary, cleanup); }
                throw;
            }
        }
        public static byte[] ReadBounded(string path, int maximum) {
            return ReadBounded(path, maximum, false);
        }
        public static byte[] ReadBounded(string path, int maximum, bool atomicReplacement) {
            byte[] bytes = ReadRawBounded(path, maximum, atomicReplacement);
            StrictJson.Validate(bytes);
            return bytes;
        }
        public static byte[] ReadRawBounded(string path, int maximum, bool atomicReplacement) {
            if (maximum < 1 || maximum > 67108864) throw new ArgumentOutOfRangeException("maximum");
            RegularAncestry(path, true);
            FileShare sharing = atomicReplacement ? FileShare.Read | FileShare.Delete : FileShare.Read;
            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, sharing)) {
                if (stream.Length < 1 || stream.Length > maximum)
                    throw new InvalidDataException("Artifact byte bound exceeded: " + path);
                byte[] bytes = new byte[(int)stream.Length];
                int position = 0;
                while (position < bytes.Length) {
                    int read = stream.Read(bytes, position, bytes.Length - position);
                    if (read == 0) throw new EndOfStreamException("Artifact changed while reading.");
                    position += read;
                }
                if (stream.ReadByte() != -1) throw new InvalidDataException("Artifact grew while reading.");
                return bytes;
            }
        }
        // A same-directory, create-new temporary file followed by no-replace rename is
        // the publication point. A partial file can never occupy the authoritative path.
        public static void PublishNew(string path, byte[] bytes) {
            RegularAncestry(path, false);
            StrictJson.Validate(bytes);
            string temporary = path + ".publishing-" + Guid.NewGuid().ToString("N");
            FileStream stream = null;
            Exception primary = null;
            try {
                stream = new FileStream(temporary, FileMode.CreateNew,
                    FileAccess.Write, FileShare.None, 4096, FileOptions.WriteThrough);
                stream.Write(bytes, 0, bytes.Length); stream.Flush(true);
            } catch (Exception failure) { primary = failure; }
            finally {
                if (stream != null) {
                    try { stream.Dispose(); }
                    catch (Exception failure) { primary = Combine(primary, failure); }
                }
            }
            if (primary == null) {
                try { File.Move(temporary, path); }
                catch (Exception failure) { primary = failure; }
            }
            if (primary != null) {
                try { if (File.Exists(temporary)) File.Delete(temporary); }
                catch (Exception failure) { primary = Combine(primary, failure); }
                throw primary;
            }
        }
        public static Exception Combine(Exception primary, Exception cleanup) {
            return primary == null ? cleanup : new AggregateException(primary, cleanup);
        }
        public static void ReplacePublished(string path, byte[] bytes) {
            RegularAncestry(path, true);
            string temporary = path + ".publishing-" + Guid.NewGuid().ToString("N");
            Exception primary = null;
            try { PublishNew(temporary, bytes); File.Replace(temporary, path, null); }
            catch (Exception failure) { primary = failure; }
            if (primary != null) {
                try { if (File.Exists(temporary)) File.Delete(temporary); }
                catch (Exception failure) { primary = Combine(primary, failure); }
                throw primary;
            }
        }
    }

    // Exact acquisition provenance only, not a lease, supervisor or release grant.
    // The future trusted gate bootstrap must CREATE this capability itself; an
    // arbitrary caller object or a successful probe cannot replace this factory.
    public sealed class ExactFence : IDisposable {
        private readonly object gate = new object();
        private readonly string path,sha256;
        private readonly FileStream stream;
        private readonly byte[] acquiredBytes;
        private bool disposed;
        private ExactFence(string path,string sha256,FileStream stream,byte[] bytes) {
            this.path=path; this.sha256=sha256; this.stream=stream; acquiredBytes=bytes;
        }
        public static ExactFence AcquireExisting(string canonicalPath,string expectedLowercaseSha256) {
            if(!System.Text.RegularExpressions.Regex.IsMatch(expectedLowercaseSha256 ?? "","\\A[0-9a-f]{64}\\z"))
                throw new ArgumentException("Exact fence requires a lowercase SHA256.");
            Files.RegularAncestry(canonicalPath,true);
            // Open only: never create, truncate, rename or delete. FileShare.None
            // also omits Inheritable; no caller controls access or sharing flags.
            FileStream owned=new FileStream(canonicalPath,FileMode.Open,FileAccess.Read,FileShare.None);
            try {
                byte[] bytes=ReadExact(owned);
                StrictJson.Validate(bytes);
                if(Files.Hash(bytes)!=expectedLowercaseSha256) throw new InvalidDataException("Exact fence hash differs.");
                return new ExactFence(canonicalPath,expectedLowercaseSha256,owned,bytes);
            } catch(Exception failure) {
                try { owned.Dispose(); } catch(Exception cleanup) { throw new AggregateException(failure,cleanup); }
                throw;
            }
        }
        private static byte[] ReadExact(FileStream owned) {
            if(!owned.CanRead || !owned.CanSeek || owned.SafeFileHandle.IsClosed || owned.SafeFileHandle.IsInvalid)
                throw new InvalidDataException("Exact fence handle is not live and readable.");
            long length=owned.Length;
            // Bound BEFORE allocation and before hashing any existing artifact.
            if(length<1 || length>8192) throw new InvalidDataException("Exact fence byte bound exceeded.");
            byte[] bytes=new byte[(int)length];
            owned.Position=0;
            try {
                int offset=0;
                while(offset<bytes.Length) {
                    int count=owned.Read(bytes,offset,bytes.Length-offset);
                    if(count==0) throw new EndOfStreamException("Exact fence changed during read.");
                    offset+=count;
                }
                if(owned.ReadByte()!=-1) throw new InvalidDataException("Exact fence grew during read.");
                return bytes;
            } finally { owned.Position=0; }
        }
        private void RequireLive() {
            if(disposed) throw new ObjectDisposedException("ExactFence");
        }
        private void AssertHeldCore() {
            RequireLive();
            byte[] current=ReadExact(stream);
            if(current.Length!=acquiredBytes.Length || Files.Hash(current)!=sha256)
                throw new InvalidDataException("Exact held fence bytes differ from acquisition.");
        }
        public string Path { get { lock(gate) { RequireLive(); return path; } } }
        public string Sha256 { get { lock(gate) { RequireLive(); return sha256; } } }
        public byte[] ReadBytes() {
            lock(gate) { AssertHeldCore(); return (byte[])acquiredBytes.Clone(); }
        }
        public void AssertHeld() { lock(gate) { AssertHeldCore(); } }
        public void Dispose() {
            lock(gate) {
                if(disposed) return;
                disposed=true; stream.Dispose();
            }
        }
    }

    // ConvertFrom-Json alone accepts duplicate keys and some non-JSON input on
    // different PowerShell versions. Validate grammar/types before deserialization.
    public sealed class StrictJson {
        private string text;
        private int offset;
        private StrictJson(string value) { text = value; }
        public static void Validate(byte[] bytes) {
            ValidateBounded(bytes, 1048576);
        }
        public static void ValidateObserverOutput(byte[] bytes) {
            // Full observer sample arrays have their own fixed artifact limit.
            // Readiness, bootstrap and other protocol JSON keep the 1 MiB API.
            ValidateBounded(bytes, 67108864);
        }
        private static void ValidateBounded(byte[] bytes, int maximumBytes) {
            if (bytes == null) throw new ArgumentNullException("bytes");
            if (bytes.Length == 0 || bytes.Length > maximumBytes)
                throw new InvalidDataException("JSON byte bound exceeded.");
            if (bytes.Length >= 3 && bytes[0] == 239 && bytes[1] == 187 && bytes[2] == 191)
                throw new InvalidDataException("JSON BOM is forbidden.");
            StrictJson parser = new StrictJson(Files.Utf8.GetString(bytes));
            parser.Space();
            if (parser.Peek() != '{') throw new InvalidDataException("One JSON object is required.");
            parser.Value(0);
            parser.Space();
            if (parser.offset != parser.text.Length) throw new InvalidDataException("Trailing JSON data.");
        }
        private char Peek() { return offset < text.Length ? text[offset] : '\0'; }
        private void Space() { while (offset < text.Length && " \t\r\n".IndexOf(text[offset]) >= 0) offset++; }
        private void Need(char token) {
            if (Peek() != token) throw new InvalidDataException("Invalid JSON at " + offset);
            offset++;
        }
        private string String() {
            Need('"');
            StringBuilder value = new StringBuilder();
            while (true) {
                char c = Peek(); offset++;
                if (c == '"') break;
                if (c < 32) throw new InvalidDataException("Invalid JSON string.");
                if (c == '\\') {
                    c = Peek(); offset++;
                    if (c == 'u') {
                        if (offset + 4 > text.Length) throw new InvalidDataException("Short JSON escape.");
                        int code;
                        if (!Int32.TryParse(text.Substring(offset, 4), NumberStyles.HexNumber,
                                CultureInfo.InvariantCulture, out code)) throw new InvalidDataException("JSON escape.");
                        offset += 4; value.Append((char)code);
                    } else {
                        int escape = "\"\\/bfnrt".IndexOf(c);
                        if (escape < 0) throw new InvalidDataException("JSON escape.");
                        value.Append("\"\\/\b\f\n\r\t"[escape]);
                    }
                } else value.Append(c);
            }
            string result = value.ToString();
            // Invalid UTF-16 escape pairs cannot round-trip to canonical UTF-8.
            Files.Utf8.GetBytes(result);
            return result;
        }
        private void Value(int depth) {
            if (depth > 40) throw new InvalidDataException("JSON nesting bound exceeded.");
            Space(); char c = Peek();
            if (c == '{') {
                offset++; Space();
                HashSet<string> keys = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
                if (Peek() == '}') { offset++; return; }
                while (true) {
                    string key = String();
                    if (!keys.Add(key)) throw new InvalidDataException("Duplicate JSON key: " + key);
                    Space(); Need(':'); Value(depth + 1); Space();
                    if (Peek() == '}') { offset++; return; }
                    Need(','); Space();
                }
            }
            if (c == '[') {
                offset++; Space(); if (Peek() == ']') { offset++; return; }
                while (true) {
                    Value(depth + 1); Space(); if (Peek() == ']') { offset++; return; }
                    Need(',');
                }
            }
            if (c == '"') { String(); return; }
            foreach (string token in new string[] { "true", "false", "null" }) {
                if (offset + token.Length <= text.Length && text.Substring(offset, token.Length) == token) {
                    offset += token.Length; return;
                }
            }
            if (c == '-') { offset++; c = Peek(); }
            if (c == '0') offset++;
            else {
                if (c < '1' || c > '9') throw new InvalidDataException("Invalid JSON number.");
                while (Peek() >= '0' && Peek() <= '9') offset++;
            }
            if (Peek() == '.') {
                offset++; int beginning = offset;
                while (Peek() >= '0' && Peek() <= '9') offset++;
                if (beginning == offset) throw new InvalidDataException("JSON fraction.");
            }
            if (Peek() == 'e' || Peek() == 'E') {
                offset++; if (Peek() == '+' || Peek() == '-') offset++;
                int beginning = offset;
                while (Peek() >= '0' && Peek() <= '9') offset++;
                if (beginning == offset) throw new InvalidDataException("JSON exponent.");
            }
        }
    }

    // The timer belongs to the owning PowerShell process. It cannot outlive that
    // process and does not manufacture apparent liveness from another process.
    public sealed class Heartbeat : IDisposable {
        private Timer timer;
        private readonly object gate = new object();
        private readonly string path, session, leaseHash, ownerStart;
        private readonly int ownerPid;
        private long sequence;
        private bool disposed;
        private Exception failure;
        public Heartbeat(string path, string session, string leaseHash, int intervalMilliseconds) {
            if (!System.Text.RegularExpressions.Regex.IsMatch(session, "^[0-9a-f]{32}$") ||
                !System.Text.RegularExpressions.Regex.IsMatch(leaseHash, "^[0-9a-f]{64}$") ||
                intervalMilliseconds < 100 || intervalMilliseconds > 5000)
                throw new ArgumentException("Invalid heartbeat contract.");
            Files.RegularAncestry(path, false);
            if (File.Exists(path) || Directory.Exists(path)) throw new IOException("Heartbeat must be absent.");
            this.path = path; this.session = session; this.leaseHash = leaseHash;
            using (Process owner = Process.GetCurrentProcess()) {
                ownerPid = owner.Id;
                ownerStart = new DateTimeOffset(owner.StartTime.ToUniversalTime()).ToString("o");
            }
            Publish();
            timer = new Timer(Tick, null, intervalMilliseconds, intervalMilliseconds);
        }
        private void Publish() {
            long next = checked(sequence + 1);
            string json = "{\"format\":\"stratus-parent-heartbeat-v1\",\"session_id\":\"" + session +
                "\",\"lease_sha256\":\"" + leaseHash + "\",\"owner_pid\":" + ownerPid +
                ",\"owner_start_time_utc\":\"" + ownerStart + "\",\"sequence\":" + next +
                ",\"published_at\":\"" + DateTimeOffset.UtcNow.ToString("o") + "\"}";
            byte[] bytes = Files.Utf8.GetBytes(json);
            Files.RegularAncestry(path, sequence != 0);
            if (sequence == 0) Files.PublishNew(path, bytes);
            else Files.ReplacePublished(path, bytes);
            sequence = next;
        }
        private void Tick(object ignored) {
            lock (gate) {
                if (disposed || failure != null) return;
                try { Publish(); }
                catch (Exception error) { failure = error; }
            }
        }
        public void AssertHealthy() {
            lock (gate) {
                if (disposed) throw new ObjectDisposedException("Heartbeat");
                if (failure != null) throw new InvalidOperationException("Heartbeat publication failed.", failure);
            }
        }
        public void Dispose() {
            lock (gate) {
                if (disposed) return;
                disposed = true;
                if (timer != null) timer.Dispose();
                if (failure != null) throw new InvalidOperationException("Heartbeat cleanup reports publication failure.", failure);
            }
        }
    }

    // Process authority is this held kernel handle, not a later lookup by numeric
    // PID. Creation time/image are checked on the same handle before it is exposed.
    public sealed class ProcessLease : IDisposable {
        [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool TerminateProcess(IntPtr process, uint exitCode);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool CloseHandle(IntPtr handle);
        private IntPtr handle;
        private bool disposed;
        private readonly FileStream executableLock;
        private readonly string expectedHash;
        public readonly int ProcessId;
        public readonly string CreationTimeUtc;
        public readonly string Executable;
        private ProcessLease(int pid, string expectedStart, string executable, string hash, FileStream heldExecutable) {
            ProcessId = pid; CreationTimeUtc = expectedStart; Executable = executable;
            expectedHash = hash; executableLock = heldExecutable;
            if (pid < 1 || !String.Equals(heldExecutable.Name, executable, StringComparison.OrdinalIgnoreCase))
                throw new ArgumentException("Exact process executable read lock required.");
            Files.RegularAncestry(executable, true);
            if (Files.HashStream(heldExecutable) != hash) throw new InvalidDataException("Process executable hash differs.");
            // QUERY_LIMITED_INFORMATION | SYNCHRONIZE | TERMINATE. No process write,
            // thread injection, debug, or broad process-tree authority is requested.
            handle = OpenProcess(0x101001, false, pid);
            if (handle == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Open exact process lease");
            try { AssertIdentity(); if (!IsAlive()) throw new InvalidOperationException("Process exited before lease acquisition."); }
            catch (Exception primary) {
                try { Dispose(); } catch (Exception cleanup) { throw new AggregateException(primary, cleanup); }
                throw;
            }
        }
        public static ProcessLease Open(int pid, string expectedStart, string executable, string hash, FileStream heldExecutable) {
            DateTimeOffset timestamp = DateTimeOffset.ParseExact(expectedStart, "o", CultureInfo.InvariantCulture);
            if (timestamp.Offset != TimeSpan.Zero || timestamp.ToString("o") != expectedStart)
                throw new ArgumentException("Exact canonical process creation time required.");
            return new ProcessLease(pid, expectedStart, executable, hash, heldExecutable);
        }
        private void AssertIdentity() {
            if (disposed || handle == IntPtr.Zero) throw new ObjectDisposedException("ProcessLease");
            uint actualPid = GetProcessId(handle);
            if (actualPid == 0) throw new Win32Exception(Marshal.GetLastWin32Error(), "Read held process ID");
            long created, exited, kernel, user;
            if (!GetProcessTimes(handle, out created, out exited, out kernel, out user))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Read held process creation time");
            StringBuilder image = new StringBuilder(32768); uint length = (uint)image.Capacity;
            if (!QueryFullProcessImageName(handle, 0, image, ref length))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Read held process image");
            string started = new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o");
            if (actualPid != ProcessId || started != CreationTimeUtc ||
                !String.Equals(Path.GetFullPath(image.ToString()), Executable, StringComparison.OrdinalIgnoreCase) ||
                Files.HashStream(executableLock) != expectedHash)
                throw new InvalidDataException("Held process identity or executable bytes differ.");
        }
        public bool IsAlive() {
            if (disposed || handle == IntPtr.Zero) throw new ObjectDisposedException("ProcessLease");
            uint result = WaitForSingleObject(handle, 0);
            if (result == 0) return false;
            if (result == 258) return true;
            if (result == UInt32.MaxValue) throw new Win32Exception(Marshal.GetLastWin32Error(), "Held process liveness wait");
            throw new InvalidOperationException("Unknown held process wait result: " + result);
        }
        public void AssertAlive() {
            if (!IsAlive()) throw new InvalidOperationException("Exact leased process has exited.");
            AssertIdentity();
        }
        public void TerminateAndWait(int milliseconds) {
            if (milliseconds < 1 || milliseconds > 10000) throw new ArgumentException("Bounded exact termination required.");
            if (!IsAlive()) return;
            AssertIdentity();
            if (!TerminateProcess(handle, 201)) throw new Win32Exception(Marshal.GetLastWin32Error(), "Terminate held exact process");
            uint result = WaitForSingleObject(handle, (uint)milliseconds);
            if (result == 0) return;
            if (result == 258) throw new TimeoutException("Exact held process termination wait exceeded.");
            if (result == UInt32.MaxValue) throw new Win32Exception(Marshal.GetLastWin32Error(), "Exact held termination wait");
            throw new InvalidOperationException("Unknown held termination wait result: " + result);
        }
        public void Dispose() {
            if (disposed) return; disposed = true;
            IntPtr owned = handle; handle = IntPtr.Zero;
            if (owned != IntPtr.Zero && !CloseHandle(owned))
                throw new Win32Exception(Marshal.GetLastWin32Error(), "Close exact held process lease");
        }
    }

    // A bootstrap capability only, not release authority. Unlike NativeChild this
    // process has NO owner-lifetime Job. After any Resume attempt, uncertainty must
    // preserve the independent containment watchdog, including stream failures.
    public sealed class IndependentWatchdogLaunch : IDisposable {
        [StructLayout(LayoutKind.Sequential)] private struct Startup {
            public int cb; public IntPtr reserved, desktop, title;
            public int x,y,xSize,ySize,xChars,yChars,fill,flags;
            public short show,reserved2; public IntPtr reservedPtr,stdin,stdout,stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr process,thread; public int pid,tid; }
        [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
        private static extern bool CreateProcess(string app, StringBuilder args, IntPtr processAttributes,
            IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string directory,
            ref StartupEx startup, out ProcessInfo process);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool DuplicateHandle(IntPtr sourceProcess, IntPtr source, IntPtr targetProcess, out IntPtr target, uint access, bool inherit, uint options);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint WaitForSingleObject(IntPtr process, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint exitCode);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint SuspendThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint ResumeThread(IntPtr thread);
        private sealed class HeldFile {
            public FileStream Stream; public string Hash;
        }
        public sealed class Readiness {
            public readonly int ProcessId;
            public readonly string StartedAt, SessionId, Nonce, LeaseSha256, ReadySha256, FrameSha256;
            internal Readiness(IndependentWatchdogLaunch launch, string lease, string ready, byte[] frame) {
                ProcessId=launch.ProcessId; StartedAt=launch.StartedAt; SessionId=launch.SessionId; Nonce=launch.Nonce;
                LeaseSha256=lease; ReadySha256=ready; FrameSha256=Files.Hash(frame);
            }
        }
        // Exact native exit observation only. This is not a terminal-document
        // validator, a stopped-container proof, or permission to release ownership.
        // The complete cooperative protocol still needs native qualification.
        public sealed class TerminalExitProof {
            public readonly int ProcessId, ExitCode;
            public readonly string StartedAt, SessionId, Nonce, ReadinessFrameSha256, ExitedAt;
            public readonly long StdoutBytes, StderrBytes;
            private TerminalExitProof(IndependentWatchdogLaunch launch) {
                ProcessId=launch.ProcessId; StartedAt=launch.StartedAt; SessionId=launch.SessionId;
                Nonce=launch.Nonce; ReadinessFrameSha256=launch.readiness.FrameSha256;
                ExitCode=0; ExitedAt=launch.terminalExitedAt;
                StdoutBytes=launch.outputCount; StderrBytes=launch.errorCount;
            }
            internal static TerminalExitProof FromOwnedObservation(IndependentWatchdogLaunch launch) {
                // No caller-provided PID, exit code, clock or receipt can construct
                // this result; only the launch's completed private observation can.
                if(launch==null || !launch.terminalProofReady || launch.readiness==null ||
                    !launch.terminalObservationStarted || launch.terminalExitedAt==null)
                    throw new InvalidOperationException("An exact owned terminal observation is required.");
                return new TerminalExitProof(launch);
            }
        }
        private readonly List<HeldFile> held = new List<HeldFile>();
        private readonly object collectionGate = new object();
        private PrivateControlClient control;
        private readonly List<Exception> faults = new List<Exception>();
        private readonly MemoryStream outputBytes = new MemoryStream();
        private AnonymousPipeServerStream input,output,error;
        private Thread outputThread,errorThread;
        private IntPtr process,thread;
        private bool resumeAttempted,resumeSucceeded,identityEstablished,disposed,outputEof,errorEof;
        private long outputCount,errorCount;
        private readonly int maximumBytes,startupMilliseconds;
        private readonly Stopwatch startupClock = Stopwatch.StartNew();
        private readonly string executable, sourceHash, runtimeHash, contractHash, leasePath,readyPath,hostHash;
        private Readiness readiness;
        private int acceptedFrameBytes;
        private bool terminalObservationStarted,terminalProofReady;
        private Stopwatch terminalClock;
        private string terminalExitedAt;
        private TerminalExitProof terminalProof;
        public readonly int ProcessId;
        public readonly string StartedAt, SessionId, Nonce, ArgvSha256, EnvironmentSha256;

        private static void Check(bool success, string label) {
            if (!success) throw new Win32Exception(Marshal.GetLastWin32Error(), label);
        }
        private static void HashSyntax(string value) {
            if (value==null || !System.Text.RegularExpressions.Regex.IsMatch(value,"\\A[0-9a-f]{64}\\z"))
                throw new ArgumentException("An exact lowercase SHA256 is required.");
        }
        private static string EnvironmentBlock(IDictionary<string,string> environment) {
            if (environment==null) throw new ArgumentNullException("environment");
            // Clone before process creation. No ambient DOTNET/profiler/PowerShell/
            // Docker overrides can enter through CreateProcess environment inheritance.
            SortedDictionary<string,string> copy = new SortedDictionary<string,string>(StringComparer.OrdinalIgnoreCase);
            HashSet<string> allowed = new HashSet<string>(new string[] {
                "SystemRoot","WINDIR","PATH","TEMP","TMP","USERPROFILE","LOCALAPPDATA","APPDATA",
                "ProgramData","ProgramFiles","ProgramFiles(x86)","CommonProgramFiles","ComSpec","SystemDrive"
            },StringComparer.OrdinalIgnoreCase);
            foreach (KeyValuePair<string,string> entry in environment) {
                if (!allowed.Contains(entry.Key) || entry.Value==null || entry.Value.IndexOf('\0')>=0 ||
                    entry.Key.IndexOf('=')>=0 || copy.ContainsKey(entry.Key))
                    throw new ArgumentException("Environment contains an unreviewed, null or aliased variable.");
                copy.Add(entry.Key,entry.Value);
            }
            if (!copy.ContainsKey("SystemRoot") || !copy.ContainsKey("WINDIR") || !copy.ContainsKey("PATH"))
                throw new ArgumentException("Explicit Windows root and PATH environment required.");
            StringBuilder block=new StringBuilder();
            foreach (KeyValuePair<string,string> entry in copy) block.Append(entry.Key).Append('=').Append(entry.Value).Append('\0');
            block.Append('\0');
            if (block.Length>32767) throw new ArgumentException("Explicit environment exceeds its bound.");
            return block.ToString();
        }
        private FileStream Hold(string path,string hash,int maximum) {
            HashSyntax(hash);
            Files.RegularAncestry(path,true);
            FileStream stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);
            try {
                // Bound the read BEFORE hashing, including child-published receipts.
                if(stream.Length<1 || stream.Length>maximum) throw new InvalidDataException("Independent held file exceeds its byte bound.");
                if(Files.HashStream(stream)!=hash) throw new InvalidDataException("Independent held file hash differs.");
                held.Add(new HeldFile {Stream=stream,Hash=hash}); return stream;
            } catch(Exception failure) {
                try { stream.Dispose(); } catch(Exception cleanup) { throw new AggregateException(failure,cleanup); }
                throw;
            }
        }
        public static IndependentWatchdogLaunch StartSuspended(string executable,string executableHash,
                string source,string sourceHash,string runtime,string runtimeHash,string contract,string contractHash,
                string sessionId,string leasePath,string readyPath,string directory,
                IDictionary<string,string> environment,int startupMilliseconds,int maximumBytes) {
            return new IndependentWatchdogLaunch(executable,executableHash,source,sourceHash,runtime,runtimeHash,
                contract,contractHash,sessionId,leasePath,readyPath,directory,environment,startupMilliseconds,maximumBytes);
        }
        public static IndependentWatchdogLaunch StartSuspendedWithControl(string executable,string executableHash,
                string source,string sourceHash,string runtime,string runtimeHash,string contract,string contractHash,
                string sessionId,string leasePath,string readyPath,string directory,
                IDictionary<string,string> environment,int startupMilliseconds,int maximumBytes) {
            return new IndependentWatchdogLaunch(executable,executableHash,source,sourceHash,runtime,runtimeHash,
                contract,contractHash,sessionId,leasePath,readyPath,directory,environment,startupMilliseconds,maximumBytes,true);
        }
        private IndependentWatchdogLaunch(string executable,string executableHash,string source,string sourceHash,
                string runtime,string runtimeHash,string contract,string contractHash,string sessionId,string leasePath,
                string readyPath,string directory,IDictionary<string,string> environment,int startupMilliseconds,int maximumBytes,
                bool withControl=false) {
            if (startupMilliseconds<1 || startupMilliseconds>120000 || maximumBytes<1024 || maximumBytes>65536)
                throw new ArgumentOutOfRangeException("Bounded startup deadline and output are required.");
            if (!System.Text.RegularExpressions.Regex.IsMatch(sessionId ?? "","\\A[0-9a-f]{32}\\z"))
                throw new ArgumentException("Exact session ID required.");
            Files.RegularAncestry(directory,true);
            if (!Directory.Exists(directory)) throw new ArgumentException("Canonical working directory required.");
            Files.RegularAncestry(leasePath,false); Files.RegularAncestry(readyPath,false);
            if (String.Equals(leasePath,readyPath,StringComparison.OrdinalIgnoreCase) || File.Exists(leasePath) ||
                Directory.Exists(leasePath) || File.Exists(readyPath) || Directory.Exists(readyPath))
                throw new ArgumentException("Distinct absent readiness artifacts required.");
            string env=EnvironmentBlock(environment);
            this.executable=executable; this.hostHash=executableHash; this.sourceHash=sourceHash; this.runtimeHash=runtimeHash;
            this.contractHash=contractHash; this.leasePath=leasePath; this.readyPath=readyPath;
            this.maximumBytes=maximumBytes; this.startupMilliseconds=startupMilliseconds; SessionId=sessionId;
            byte[] nonce=new byte[32]; using(RandomNumberGenerator random=RandomNumberGenerator.Create()) random.GetBytes(nonce);
            Nonce=BitConverter.ToString(nonce).Replace("-","").ToLowerInvariant();
            string[] arguments={"-NoLogo","-NoProfile","-NonInteractive","-File",source,"-ContractPath",contract,
                "-ContractSha256",contractHash,"-RuntimeSha256",runtimeHash,"-BootstrapNonce",Nonce};
            StringBuilder command=new StringBuilder(NativeChild.Quote(executable));
            foreach(string argument in arguments) command.Append(' ').Append(NativeChild.Quote(argument));
            ArgvSha256=Files.Hash(Files.Utf8.GetBytes(command.ToString()));
            EnvironmentSha256=Files.Hash(Encoding.Unicode.GetBytes(env));
            IntPtr attributes=IntPtr.Zero,inheritList=IntPtr.Zero,environmentMemory=IntPtr.Zero;
            bool attributesInitialized=false;
            List<IntPtr> duplicates=new List<IntPtr>();
            Exception failure=null;
            try {
                Hold(executable,executableHash,134217728); Hold(source,sourceHash,2097152);
                Hold(runtime,runtimeHash,2097152); Hold(contract,contractHash,1048576);
                // These exact read-only duplicates survive owner death inside the child.
                // The child gets no write-capable source handle and no inherit-all scope.
                foreach(HeldFile file in held) {
                    IntPtr duplicate;
                    Check(DuplicateHandle(GetCurrentProcess(),file.Stream.SafeFileHandle.DangerousGetHandle(),
                        GetCurrentProcess(),out duplicate,0,true,2),"Duplicate inherited source read lock");
                    duplicates.Add(duplicate);
                }
                output=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                error=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                input=new AnonymousPipeServerStream(PipeDirection.Out,HandleInheritability.Inheritable);
                List<IntPtr> handles=new List<IntPtr>(duplicates);
                if(withControl) {
                    control=new PrivateControlClient(SessionId);
                    command.Append(" -ControlInputHandle ").Append(NativeChild.Quote(control.ChildInputHandleText));
                    command.Append(" -ControlOutputHandle ").Append(NativeChild.Quote(control.ChildOutputHandleText));
                    ArgvSha256=Files.Hash(Files.Utf8.GetBytes(command.ToString()));
                    handles.Add(control.ChildInputHandle); handles.Add(control.ChildOutputHandle);
                }
                handles.Add(input.ClientSafePipeHandle.DangerousGetHandle());
                handles.Add(output.ClientSafePipeHandle.DangerousGetHandle());
                handles.Add(error.ClientSafePipeHandle.DangerousGetHandle());
                IntPtr size=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);
                if(size==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(),"Independent startup attribute size");
                attributes=Marshal.AllocHGlobal(size);
                Check(InitializeProcThreadAttributeList(attributes,1,0,ref size),"Independent startup attributes");
                attributesInitialized=true; inheritList=Marshal.AllocHGlobal(IntPtr.Size*handles.Count);
                for(int i=0;i<handles.Count;i++) Marshal.WriteIntPtr(inheritList,i*IntPtr.Size,handles[i]);
                Check(UpdateProcThreadAttribute(attributes,0,new IntPtr(0x20002),inheritList,
                    new IntPtr(IntPtr.Size*handles.Count),IntPtr.Zero,IntPtr.Zero),"Exact independent inherited handle allowlist");
                StartupEx startup=new StartupEx(); startup.startup.cb=Marshal.SizeOf(startup); startup.startup.flags=0x101;
                startup.startup.stdin=input.ClientSafePipeHandle.DangerousGetHandle();
                startup.startup.stdout=output.ClientSafePipeHandle.DangerousGetHandle();
                startup.startup.stderr=error.ClientSafePipeHandle.DangerousGetHandle(); startup.attributes=attributes;
                environmentMemory=Marshal.StringToHGlobalUni(env);
                ProcessInfo created;
                // The historical bootstrap flag is preserved only for its old
                // overload. New control mode cannot use BREAKAWAY as a workaround.
                uint creationFlags=withControl ? 0x08080404u : 0x09080404u;
                Check(CreateProcess(executable,command,IntPtr.Zero,IntPtr.Zero,true,creationFlags,
                    environmentMemory,directory,ref startup,out created),"Create independent watchdog suspended");
                process=created.process; thread=created.thread; ProcessId=created.pid;
                long started,exited,kernel,user;
                Check(GetProcessTimes(process,out started,out exited,out kernel,out user),"Independent creation time");
                StartedAt=new DateTimeOffset(DateTime.FromFileTimeUtc(started)).ToString("o");
                AssertIdentity(); identityEstablished=true; AssertNoJob();
                output.DisposeLocalCopyOfClientHandle(); error.DisposeLocalCopyOfClientHandle();
                input.DisposeLocalCopyOfClientHandle(); input.Dispose(); input=null;
                if(control!=null) control.StartAfterInheritance();
                outputThread=new Thread(delegate() { Collect(output,true); }); outputThread.IsBackground=true;
                errorThread=new Thread(delegate() { Collect(error,false); }); errorThread.IsBackground=true;
                outputThread.Start(); errorThread.Start();
            } catch(Exception errorValue) { failure=errorValue; }
            finally {
                if(attributesInitialized) DeleteProcThreadAttributeList(attributes);
                if(attributes!=IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if(inheritList!=IntPtr.Zero) Marshal.FreeHGlobal(inheritList);
                if(environmentMemory!=IntPtr.Zero) Marshal.FreeHGlobal(environmentMemory);
                foreach(IntPtr duplicate in duplicates) {
                    if(!CloseHandle(duplicate)) failure=Files.Combine(failure,new Win32Exception(Marshal.GetLastWin32Error(),"Close parent source duplicate"));
                }
            }
            if(failure!=null) {
                // No Resume can have happened in this constructor. Only this exact
                // newly-created suspended handle may be terminated on constructor failure.
                try { AbortBeforeResume(); Dispose(); } catch(Exception cleanup) { failure=Files.Combine(failure,cleanup); }
                throw failure;
            }
        }
        public void BeginControlRequest(byte[] payload) {
            if(control==null) throw new InvalidOperationException("This bootstrap has no private control capability.");
            control.Begin(this,payload);
        }
        public PrivateControlResponse PollControlResponse() {
            if(control==null) throw new InvalidOperationException("This bootstrap has no private control capability.");
            return control.Poll(this);
        }
        public void AssertControlHealthy() {
            if(control==null) throw new InvalidOperationException("This bootstrap has no private control capability.");
            control.AssertHealthy();
        }
        private void Record(Exception failure) { lock(collectionGate) { if(faults.Count<8) faults.Add(failure); } }
        private void ThrowFaults() { lock(collectionGate) { if(faults.Count!=0) throw new AggregateException(faults.ToArray()); } }
        private void Collect(Stream pipe,bool stdout) {
            try {
                byte[] buffer=new byte[4096];
                while(true) {
                    int count=pipe.Read(buffer,0,buffer.Length);
                    lock(collectionGate) {
                        if(count==0) { if(stdout) outputEof=true; else errorEof=true; return; }
                        long before=stdout ? outputCount : errorCount;
                        long after=Math.Min((long)maximumBytes+1,before+count);
                        if(stdout) {
                            outputCount=after;
                            int retained=(int)Math.Min(count,Math.Max(0,maximumBytes-outputBytes.Length));
                            if(retained>0) outputBytes.Write(buffer,0,retained);
                        } else errorCount=after;
                        if(before<=maximumBytes && after>maximumBytes) Record(new InvalidDataException("Independent output byte bound exceeded."));
                        if(!stdout && before==0) Record(new InvalidDataException("Independent watchdog wrote stderr."));
                    }
                    // Keep draining after bounded failure. Never kill the resumed
                    // watchdog or close its stdout merely because readiness failed.
                }
            } catch(Exception failure) { Record(failure); }
        }
        private void AssertNoJob() {
            bool member; Check(IsProcessInJob(process,IntPtr.Zero,out member),"Independent ANY-Job proof");
            if(member) throw new InvalidOperationException("Independent watchdog still belongs to a Job.");
        }
        private void AssertIdentity() {
            if(disposed || process==IntPtr.Zero) throw new ObjectDisposedException("IndependentWatchdogLaunch");
            long created,exited,kernel,user; Check(GetProcessTimes(process,out created,out exited,out kernel,out user),"Held independent creation time");
            StringBuilder image=new StringBuilder(32768); uint length=(uint)image.Capacity;
            Check(QueryFullProcessImageName(process,0,image,ref length),"Held independent image");
            if(GetProcessId(process)!=ProcessId || new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o")!=StartedAt ||
                !String.Equals(Path.GetFullPath(image.ToString()),executable,StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Independent held process identity differs.");
            foreach(HeldFile file in held) if(Files.HashStream(file.Stream)!=file.Hash) throw new InvalidDataException("Independent held source/evidence bytes changed.");
        }
        public bool IsAlive() {
            if(disposed) throw new ObjectDisposedException("IndependentWatchdogLaunch");
            if(process==IntPtr.Zero) return false;
            uint wait=WaitForSingleObject(process,0);
            if(wait==0) return false; if(wait==258) return true;
            Check(wait!=UInt32.MaxValue,"Independent process liveness");
            throw new InvalidOperationException("Unknown independent liveness wait result.");
        }
        public void AssertSuspended() {
            if(resumeAttempted || !IsAlive()) throw new InvalidOperationException("Exact never-resumed child required.");
            AssertIdentity(); AssertNoJob();
            try {
                uint previous=SuspendThread(thread); Check(previous!=UInt32.MaxValue,"Independent suspend proof");
                if(previous!=1) throw new InvalidOperationException("Independent suspension was disproved; retain the inspection increment.");
                uint restored=ResumeThread(thread); Check(restored!=UInt32.MaxValue,"Independent suspend restoration");
                if(restored!=2) throw new InvalidOperationException("Independent suspension restoration was ambiguous.");
            } catch(Exception failure) {
                // A failed native suspend-count proof cannot restore confidence
                // that this process has never executed, even without our Resume.
                resumeAttempted=true; Record(failure); throw;
            }
        }
        public void Resume() {
            if(resumeAttempted) throw new InvalidOperationException("Independent Resume may be attempted once.");
            if(startupClock.ElapsedMilliseconds>=startupMilliseconds) throw new TimeoutException("Independent startup deadline expired before Resume.");
            AssertSuspended(); ThrowFaults();
            if(startupClock.ElapsedMilliseconds>=startupMilliseconds) throw new TimeoutException("Independent startup deadline expired during suspended proof.");
            // Latch BEFORE ResumeThread: an ambiguous resume result cannot restore
            // owner authority to terminate a possibly armed containment watchdog.
            resumeAttempted=true;
            try {
                uint previous=ResumeThread(thread); Check(previous!=UInt32.MaxValue,"Resume independent watchdog");
                if(previous!=1) throw new InvalidOperationException("Independent resume count differs.");
                resumeSucceeded=true;
            } catch(Exception failure) { Record(failure); throw; }
        }
        public Readiness PollReady() {
            if(terminalObservationStarted)
                throw new InvalidOperationException("Live readiness is unavailable after terminal observation begins.");
            return PollLiveReady();
        }
        private Readiness PollLiveReady() {
            if(terminalObservationStarted)
                throw new InvalidOperationException("Live readiness is unavailable after terminal observation begins.");
            if(!resumeSucceeded) throw new InvalidOperationException("Independent watchdog has no successful owned Resume.");
            try {
                if(!IsAlive()) throw new InvalidOperationException("Independent watchdog exited before a live readiness proof.");
                AssertIdentity(); AssertNoJob(); ThrowFaults();
                byte[] bytes;
                lock(collectionGate) {
                    if(terminalObservationStarted)
                        throw new InvalidOperationException("Live readiness is unavailable after terminal observation begins.");
                    if(outputEof || errorEof) throw new EndOfStreamException("Independent startup pipe reached EOF while watchdog is live.");
                    bytes=outputBytes.ToArray();
                }
                if(readiness!=null) {
                    lock(collectionGate) {
                        if(terminalObservationStarted)
                            throw new InvalidOperationException("Live readiness is unavailable after terminal observation begins.");
                        AssertCollectedFrame(acceptedFrameBytes);
                        if(!IsAlive()) throw new InvalidOperationException("Independent watchdog exited before cached readiness return.");
                        return readiness;
                    }
                }
                if(startupClock.ElapsedMilliseconds>=startupMilliseconds) throw new TimeoutException("Independent startup deadline expired.");
                int newline=Array.IndexOf(bytes,(byte)10);
                if(newline<0) return null;
                if(newline!=bytes.Length-1) throw new InvalidDataException("Independent startup requires exactly one bounded frame.");
                byte[] frame=new byte[newline]; Array.Copy(bytes,frame,newline); StrictJson.Validate(frame);
                string prefix="{\"format\":\"stratus-independent-watchdog-ready-v1\",\"session_id\":\""+SessionId+
                    "\",\"nonce\":\""+Nonce+"\",\"contract_sha256\":\""+contractHash+"\",\"source_sha256\":\""+sourceHash+
                    "\",\"runtime_sha256\":\""+runtimeHash+"\",\"host_sha256\":\""+hostHash+"\",\"pid\":\""+
                    ProcessId.ToString(CultureInfo.InvariantCulture)+"\",\"start_time_utc\":\""+StartedAt+"\",\"lease_sha256\":\"";
                string pattern="\\A"+System.Text.RegularExpressions.Regex.Escape(prefix)+
                    "(?<lease>[0-9a-f]{64})\",\"ready_sha256\":\"(?<ready>[0-9a-f]{64})\"\\}\\z";
                System.Text.RegularExpressions.Match match=System.Text.RegularExpressions.Regex.Match(Files.Utf8.GetString(frame),pattern,
                    System.Text.RegularExpressions.RegexOptions.CultureInvariant,TimeSpan.FromSeconds(1));
                if(!match.Success) throw new InvalidDataException("Independent startup frame is not exactly launch-bound.");
                string leaseHash=match.Groups["lease"].Value,readyHash=match.Groups["ready"].Value;
                foreach(string[] artifact in new string[][] {new string[] {leasePath,leaseHash},new string[] {readyPath,readyHash}}) {
                    Hold(artifact[0],artifact[1],65536);
                    Files.ReadBounded(artifact[0],65536);
                }
                if(startupClock.ElapsedMilliseconds>=startupMilliseconds) throw new TimeoutException("Independent ready readback exceeded its startup deadline.");
                if(!IsAlive()) throw new InvalidOperationException("Independent watchdog exited during ready readback.");
                AssertIdentity(); AssertNoJob(); ThrowFaults();
                lock(collectionGate) {
                    // Receipt reads are not a collector barrier. Recheck fresh pipe
                    // state atomically with publication of the private capability.
                    if(terminalObservationStarted)
                        throw new InvalidOperationException("Live readiness is unavailable after terminal observation begins.");
                    AssertCollectedFrame(frame.Length);
                    if(!IsAlive()) throw new InvalidOperationException("Independent watchdog exited before readiness publication.");
                    acceptedFrameBytes=frame.Length; readiness=new Readiness(this,leaseHash,readyHash,frame); return readiness;
                }
            } catch(Exception failure) { Record(failure); throw; }
        }
        public void BeginTerminalObservation() {
            Stopwatch observationClock=Stopwatch.StartNew();
            if(terminalObservationStarted)
                throw new InvalidOperationException("Independent terminal observation may begin only once.");
            if(readiness==null)
                throw new InvalidOperationException("Existing owned readiness is required before terminal observation.");
            try {
                // This must precede any cooperative terminal commit. It does not
                // publish a commit, stop a process, or acknowledge terminal state.
                PollLiveReady();
                lock(collectionGate) {
                    if(terminalObservationStarted)
                        throw new InvalidOperationException("Independent terminal observation may begin only once.");
                    AssertCollectedFrame(acceptedFrameBytes);
                    if(!IsAlive()) throw new InvalidOperationException("Independent watchdog exited before terminal observation began.");
                    terminalClock=observationClock;
                    AssertTerminalDeadline();
                    terminalObservationStarted=true;
                    // Irreversible terminal mode precedes transport preparation;
                    // even a failed drain admission cannot restore live readiness.
                    if(control!=null) control.EnterTerminal(this);
                    AssertTerminalDeadline();
                }
            } catch(Exception failure) { Record(failure); throw; }
        }
        private void AssertTerminalDeadline() {
            if(terminalClock==null || terminalClock.ElapsedMilliseconds>=30000)
                throw new TimeoutException("Independent terminal observation exceeded its fixed 30000ms deadline.");
        }
        private void AssertTerminalFrame() {
            // Caller holds collectionGate. EOF is allowed only on this terminal
            // observation path; the sole accepted ready frame cannot gain bytes.
            if(faults.Count!=0) throw new AggregateException(faults.ToArray());
            if(readiness==null || acceptedFrameBytes<1 || errorCount!=0 ||
                outputCount!=acceptedFrameBytes+1 || outputBytes.Length!=acceptedFrameBytes+1)
                throw new InvalidDataException("Independent terminal stream differs from the accepted ready frame.");
            byte[] bytes=outputBytes.ToArray();
            if(bytes[acceptedFrameBytes]!=10)
                throw new InvalidDataException("Independent terminal ready frame lost its exact newline.");
            byte[] frame=new byte[acceptedFrameBytes]; Array.Copy(bytes,frame,acceptedFrameBytes);
            if(Files.Hash(frame)!=readiness.FrameSha256)
                throw new InvalidDataException("Independent terminal ready frame bytes changed.");
        }
        public TerminalExitProof PollTerminalExit() {
            if(!terminalObservationStarted)
                throw new InvalidOperationException("Independent terminal observation has not begun.");
            try {
                AssertTerminalDeadline();
                // AssertIdentity rehashes every retained source, contract, lease
                // and ready receipt using the same held handles as bootstrap.
                AssertIdentity(); AssertNoJob(); ThrowFaults();
                bool controlDrained=control==null || control.PollTerminalDrain(this);
                AssertTerminalDeadline();
                bool alive=IsAlive();
                lock(collectionGate) {
                    AssertTerminalFrame(); AssertTerminalDeadline();
                    if(alive) return null;
                }
                uint exitCode;
                Check(GetExitCodeProcess(process,out exitCode),"Independent terminal exit code");
                if(exitCode!=0) throw new InvalidDataException("Independent watchdog did not exit with code zero.");
                long created,exited,kernel,user;
                Check(GetProcessTimes(process,out created,out exited,out kernel,out user),"Independent terminal process times");
                if(exited<=0 || exited<created ||
                    new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o")!=StartedAt)
                    throw new InvalidDataException("Independent terminal process times differ.");
                string exitedAt=new DateTimeOffset(DateTime.FromFileTimeUtc(exited)).ToString("o");
                // Recheck all evidence after native exit reads, then atomically
                // inspect both collectors. No dispose/terminate/handle release is
                // permitted here, including on timeout, EOF delay or native fault.
                AssertIdentity(); AssertNoJob(); ThrowFaults();
                controlDrained=control==null || control.PollTerminalDrain(this);
                lock(collectionGate) {
                    AssertTerminalFrame(); AssertTerminalDeadline();
                    if(IsAlive()) throw new InvalidOperationException("Independent terminal process liveness changed.");
                    if(!outputEof || !errorEof || !controlDrained) return null;
                    if(terminalProof!=null) {
                        if(terminalProof.ExitedAt!=exitedAt)
                            throw new InvalidDataException("Independent terminal exit time changed.");
                        return terminalProof;
                    }
                    terminalExitedAt=exitedAt; terminalProofReady=true;
                    terminalProof=TerminalExitProof.FromOwnedObservation(this);
                    return terminalProof;
                }
            } catch(Exception failure) { Record(failure); throw; }
        }
        private void AssertCollectedFrame(int frameBytes) {
            // Caller holds collectionGate; never accepts an old pre-readback snapshot.
            if(faults.Count!=0) throw new AggregateException(faults.ToArray());
            if(outputEof || errorEof || errorCount!=0 || outputCount!=frameBytes+1 || outputBytes.Length!=frameBytes+1)
                throw new InvalidDataException("Independent startup stream changed before readiness publication.");
        }
        public void AbortBeforeResume() {
            if(resumeAttempted) throw new InvalidOperationException("Any Resume attempt permanently forbids owner-side abort.");
            if(!IsAlive()) return;
            if(identityEstablished) AssertIdentity();
            uint previous=SuspendThread(thread);
            if(previous==UInt32.MaxValue) {
                resumeAttempted=true;
                Exception failure=new Win32Exception(Marshal.GetLastWin32Error(),"Abort native suspended proof failed");
                Record(failure); throw failure;
            }
            if(previous!=1) {
                // A previous zero means this probe just paused a running thread.
                // Never undo that increment after the never-resumed proof failed.
                // Retain the capability without granting owner-side abort authority.
                resumeAttempted=true;
                Exception failure=new InvalidOperationException("Abort lacks an exact native never-resumed suspend proof.");
                Record(failure); throw failure;
            }
            // Retain the added suspend count THROUGH exact termination: do not
            // restore a running window between the native proof and the abort.
            try {
                Check(TerminateProcess(process,202),"Abort exact never-resumed watchdog");
                uint wait=WaitForSingleObject(process,5000); Check(wait!=UInt32.MaxValue,"Never-resumed watchdog termination wait");
                if(wait!=0) throw new TimeoutException("Never-resumed watchdog termination was not proved.");
            } catch(Exception failure) { resumeAttempted=true; Record(failure); throw; }
        }
        public void Dispose() {
            if(disposed) return;
            if(IsAlive()) {
                if(resumeAttempted) throw new InvalidOperationException("Live resumed watchdog capability must remain owned; disposal is not authority to kill.");
                AbortBeforeResume();
            }
            if(outputThread!=null && (outputThread.ThreadState & System.Threading.ThreadState.Unstarted)==0 && !outputThread.Join(2000)) throw new TimeoutException("Independent stdout EOF cleanup is unproved; handles retained.");
            if(errorThread!=null && (errorThread.ThreadState & System.Threading.ThreadState.Unstarted)==0 && !errorThread.Join(2000)) throw new TimeoutException("Independent stderr EOF cleanup is unproved; handles retained.");
            // A failed control shutdown must retain the original source/native
            // capability, not aggregate the failure after releasing it.
            if(control!=null) control.Dispose();
            Exception failure=null;
            foreach(IDisposable item in new IDisposable[] {input,output,error,outputBytes}) {
                if(item==null) continue; try { item.Dispose(); } catch(Exception errorValue) { failure=Files.Combine(failure,errorValue); }
            }
            foreach(HeldFile file in held) { try { file.Stream.Dispose(); } catch(Exception errorValue) { failure=Files.Combine(failure,errorValue); } }
            foreach(IntPtr handle in new IntPtr[] {thread,process}) {
                if(handle!=IntPtr.Zero && !CloseHandle(handle)) failure=Files.Combine(failure,new Win32Exception(Marshal.GetLastWin32Error(),"Close independent native handle"));
            }
            disposed=true;
            if(failure!=null) throw failure;
        }
    }

    // Separate safety-guard capability: no owner-lifetime Job, no breakaway or
    // policy override. Pipe/timeout ambiguity cannot retire a resumed guard.
    // Captured frames are native observations only; artifact/STOP semantics are
    // deliberately the receiver's separately qualified responsibility.
    public sealed class IndependentTransitionLaunch : IDisposable {
        [StructLayout(LayoutKind.Sequential)] private struct Startup {
            public int cb; public IntPtr reserved,desktop,title;
            public int x,y,xSize,ySize,xChars,yChars,fill,flags;
            public short show,reserved2; public IntPtr reservedPtr,stdin,stdout,stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr process,thread; public int pid,tid; }
        [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)]
        private static extern bool CreateProcess(string app,StringBuilder args,IntPtr processAttributes,
            IntPtr threadAttributes,bool inherit,uint flags,IntPtr environment,string directory,
            ref StartupEx startup,out ProcessInfo created);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr attribute,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool DuplicateHandle(IntPtr sourceProcess,IntPtr source,IntPtr targetProcess,out IntPtr target,uint access,bool inherit,uint options);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool IsProcessInJob(IntPtr process,IntPtr job,out bool result);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetProcessTimes(IntPtr process,out long created,out long exited,out long kernel,out long user);
        [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] private static extern bool QueryFullProcessImageName(IntPtr process,uint flags,StringBuilder name,ref uint size);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint WaitForSingleObject(IntPtr process,uint milliseconds);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetExitCodeProcess(IntPtr process,out uint exitCode);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool TerminateProcess(IntPtr process,uint code);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint SuspendThread(IntPtr thread);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint ResumeThread(IntPtr thread);

        private const int ReadyMaximumBytes=8192, CompleteMaximumBytes=8192;
        private const int OutputMaximumBytes=327680, ErrorMaximumBytes=65536;
        private const string RetainedPostgresId="6ce82603e44abb95be94dbe95830af2d00c0c36ca3fe8a122b798087ecc1b299";
        private static readonly string[] PlanKeys={"Root","SessionId","OwnerPid","OwnerStartTimeUtc","DeadlineUtc",
            "HeartbeatTimeoutSeconds","LeasePath","ReceiptPath","HeartbeatPath","CommitPath","LiveAcceptancePath",
            "PreStartAttestationPath","ExpectedPreStartSha256","StartFencePath","ExpectedStartFenceSha256",
            "StartRevokePath","StartAttemptPath","StartCompletionPath","PostgresContainer"};
        private sealed class HeldFile { public FileStream Stream; public string Hash; public int Maximum; }
        private sealed class SourceDuplicateOutcome {
            public bool Attempted,Returned,Succeeded,Closed;
            public IntPtr Raw;
        }
        private readonly List<HeldFile> held=new List<HeldFile>();
        private readonly object captureGate=new object();
        private readonly List<Exception> faults=new List<Exception>();
        private readonly MemoryStream outputBytes=new MemoryStream();
        private readonly Stopwatch lifetimeClock=Stopwatch.StartNew();
        private readonly DateTimeOffset createdUtc=DateTimeOffset.UtcNow;
        private readonly DateTimeOffset deadline;
        private readonly double maximumLifetimeMilliseconds;
        private readonly string executable,leasePath,receiptPath,root,source,runtime,contract;
        private readonly string executableHash,sourceHash,runtimeHash,contractHash,environmentText;
        private readonly Dictionary<string,string> launchPlan;
        private readonly CommandSessionOwner commandSession;
        private readonly object lifecycleGate=new object();
        private readonly List<SourceDuplicateOutcome> sourceDuplicates=new List<SourceDuplicateOutcome>();
        private AnonymousPipeServerStream input,output,error;
        private Thread outputThread,errorThread;
        private IntPtr process,thread;
        private bool resumeAttempted,resumeSucceeded,identityEstablished,disposed,outputEof,errorEof;
        private long outputCount,errorCount;
        private byte[] readyCapture,completeCapture;
        private string readyLeaseHash,completeReceiptHash,terminalExitedAt,terminalStdoutHash;
        private int completeReceiptBytes;
        private bool readyProofReady,terminalProofReady;
        private ReadyProof readyProof;
        private TerminalExitProof terminalProof;
        private RootTerminationFacts rootTerminationFacts;
        private RootTerminationObservation rootTerminationObservation;
        private bool startAttempted,startInProgress,startCompleted,createAttempted,createReturned,createSucceeded;
        private bool retirementRequested,commandSessionRetired,commandSessionDisposed,releaseStarted;
        private bool outputClientClosed,errorClientClosed,inputClientClosed;
        private ProcessInfo creationOutput;
        private int nativeProcessId;
        private string nativeStartedAt,nonce,argvSha256,environmentSha256;
        public int ProcessId { get { if(!identityEstablished) throw new InvalidOperationException("Transition native identity is not established."); return nativeProcessId; } }
        public string StartedAt { get { if(!identityEstablished) throw new InvalidOperationException("Transition native birth is not established."); return nativeStartedAt; } }
        public readonly string SessionId;
        public string Nonce { get { if(nonce==null) throw new InvalidOperationException("Transition nonce is not captured."); return nonce; } }
        public string ArgvSha256 { get { if(argvSha256==null) throw new InvalidOperationException("Transition full argv is not captured."); return argvSha256; } }
        public string EnvironmentSha256 { get { if(environmentSha256==null) throw new InvalidOperationException("Transition environment is not captured."); return environmentSha256; } }

        public sealed class ReadyProof {
            public readonly string Text,Sha256,StartedAt,SessionId,Nonce,LeasePath,LeaseSha256;
            public readonly int ProcessId;
            public readonly long ByteCount;
            private ReadyProof(IndependentTransitionLaunch owner) {
                Text=Files.Utf8.GetString(owner.readyCapture); Sha256=Files.Hash(owner.readyCapture);
                ByteCount=owner.readyCapture.LongLength; ProcessId=owner.ProcessId; StartedAt=owner.StartedAt;
                SessionId=owner.SessionId; Nonce=owner.Nonce; LeasePath=owner.leasePath; LeaseSha256=owner.readyLeaseHash;
            }
            internal static ReadyProof FromOwned(IndependentTransitionLaunch owner) {
                if(owner==null || !owner.readyProofReady || owner.readyCapture==null || owner.readyLeaseHash==null)
                    throw new InvalidOperationException("Owned live Transition READY capture is required.");
                return new ReadyProof(owner);
            }
        }
        public sealed class TerminalExitProof {
            public readonly ReadyProof Ready;
            public readonly string CompleteText,CompleteSha256,StartedAt,SessionId,Nonce,ExitedAt,StdoutSha256,ReceiptPath,ReceiptSha256;
            public readonly int ProcessId,ExitCode,ReceiptBytes;
            public readonly long CompleteBytes,StdoutBytes,StderrBytes;
            private TerminalExitProof(IndependentTransitionLaunch owner) {
                Ready=owner.readyProof; CompleteText=Files.Utf8.GetString(owner.completeCapture);
                CompleteSha256=Files.Hash(owner.completeCapture); CompleteBytes=owner.completeCapture.LongLength;
                ProcessId=owner.ProcessId; ExitCode=0; StartedAt=owner.StartedAt; SessionId=owner.SessionId; Nonce=owner.Nonce;
                ExitedAt=owner.terminalExitedAt; StdoutSha256=owner.terminalStdoutHash;
                StdoutBytes=owner.outputCount; StderrBytes=owner.errorCount;
                ReceiptPath=owner.receiptPath; ReceiptSha256=owner.completeReceiptHash; ReceiptBytes=owner.completeReceiptBytes;
            }
            internal static TerminalExitProof FromOwned(IndependentTransitionLaunch owner) {
                if(owner==null || !owner.terminalProofReady || owner.readyProof==null || owner.completeCapture==null ||
                    owner.terminalExitedAt==null || owner.terminalStdoutHash==null)
                    throw new InvalidOperationException("Owned Transition exit, two frames and both EOF proofs are required.");
                return new TerminalExitProof(owner);
            }
        }

        private sealed class RootTerminationFacts {
            public uint ExitCode;
            public string ExitedAt,ObservedAt;
            public long ObservedElapsedMilliseconds;
            public bool ObservedAfterProtocolDeadline,StdoutCollectorCompleted,StderrCollectorCompleted;
        }
        // An irreversible fact about the original root only, sampled separately
        // from protocol acceptance. EOF, diagnostics and byte counts below are
        // frozen at FIRST observation, not a promise that later drain completed.
        // This type never authorizes guard retirement or proves empty descendants.
        public sealed class RootTerminationObservation {
            public readonly int ProcessId,RecordedFaultCount;
            public readonly uint ExitCode;
            public readonly string StartedAt,SessionId,Nonce,ExitedAt,ObservedAt,FaultSummary;
            public readonly long ObservedElapsedMilliseconds,StdoutRetainedBytes,StdoutObservedBytesLowerBound,StderrObservedBytesLowerBound;
            public readonly bool ObservedAfterProtocolDeadline,StdoutEofAtObservation,StderrEofAtObservation;
            public readonly bool StdoutCollectorCompletedAtObservation,StderrCollectorCompletedAtObservation;
            public readonly bool StdoutCaptureTruncatedAtObservation,PipeDrainCompleteAtObservation;
            public readonly bool DescendantEmptinessProven=false,ProtocolAccepted=false,ContainmentAccepted=false,RetirementPermitted=false;
            private RootTerminationObservation(IndependentTransitionLaunch owner) {
                RootTerminationFacts facts=owner.rootTerminationFacts;
                ProcessId=owner.ProcessId; StartedAt=owner.StartedAt; SessionId=owner.SessionId; Nonce=owner.Nonce;
                ExitCode=facts.ExitCode; ExitedAt=facts.ExitedAt; ObservedAt=facts.ObservedAt;
                ObservedElapsedMilliseconds=facts.ObservedElapsedMilliseconds;
                ObservedAfterProtocolDeadline=facts.ObservedAfterProtocolDeadline;
                StdoutRetainedBytes=owner.outputBytes.Length;
                // Collect saturates at each limit+1, so these are LOWER bounds,
                // never advertised as full lengths after an overflow.
                StdoutObservedBytesLowerBound=owner.outputCount; StderrObservedBytesLowerBound=owner.errorCount;
                StdoutCaptureTruncatedAtObservation=owner.outputCount!=owner.outputBytes.Length;
                StdoutEofAtObservation=owner.outputEof; StderrEofAtObservation=owner.errorEof;
                StdoutCollectorCompletedAtObservation=facts.StdoutCollectorCompleted;
                StderrCollectorCompletedAtObservation=facts.StderrCollectorCompleted;
                PipeDrainCompleteAtObservation=StdoutEofAtObservation && StderrEofAtObservation &&
                    StdoutCollectorCompletedAtObservation && StderrCollectorCompletedAtObservation;
                RecordedFaultCount=Math.Min(owner.faults.Count,8);
                StringBuilder summary=new StringBuilder();
                for(int index=0;index<RecordedFaultCount;index++) {
                    string message=owner.faults[index].Message ?? String.Empty;
                    if(index!=0) summary.Append('\n');
                    summary.Append(message.Substring(0,Math.Min(message.Length,512)).Replace('\r',' ').Replace('\n',' '));
                }
                FaultSummary=summary.ToString();
            }
            internal static RootTerminationObservation FromOwned(IndependentTransitionLaunch owner) {
                if(owner==null || !owner.identityEstablished || owner.rootTerminationFacts==null)
                    throw new InvalidOperationException("Original native root-death observation is required.");
                return new RootTerminationObservation(owner);
            }
        }

        private static void Check(bool success,string label) {
            if(!success) throw new Win32Exception(Marshal.GetLastWin32Error(),label);
        }
        private static void HashSyntax(string value) {
            if(value==null || !System.Text.RegularExpressions.Regex.IsMatch(value,"\\A[0-9a-f]{64}\\z"))
                throw new ArgumentException("Exact lowercase Transition SHA256 required.");
        }
        private static void SafeToken(string value) {
            if(value==null || value.IndexOf('\0')>=0 || value.IndexOf('\r')>=0 || value.IndexOf('\n')>=0)
                throw new ArgumentException("Transition native token contains a control character.");
        }
        private static string Quote(string value) {
            SafeToken(value); StringBuilder result=new StringBuilder("\""); int slashes=0;
            foreach(char c in value) {
                if(c=='\\') { slashes++; continue; }
                if(c=='"') result.Append('\\',slashes*2+1).Append(c);
                else result.Append('\\',slashes).Append(c);
                slashes=0;
            }
            return result.Append('\\',slashes*2).Append('"').ToString();
        }
        private static DateTimeOffset CanonicalUtc(string value) {
            SafeToken(value);
            DateTimeOffset parsed=DateTimeOffset.ParseExact(value,"o",CultureInfo.InvariantCulture,DateTimeStyles.None);
            if(parsed.Offset!=TimeSpan.Zero || parsed.ToString("o")!=value)
                throw new ArgumentException("Canonical Transition UTC timestamp required.");
            return parsed;
        }
        private static void CanonicalPath(string path,bool directory) {
            SafeToken(path);
            // This fixed local Windows contract has no UNC/device/ADS namespace.
            // Reject aliases before opening; this is not physical-file provenance.
            if(path.Length<4 || !((path[0]>='A' && path[0]<='Z') || (path[0]>='a' && path[0]<='z')) ||
                path[1]!=':' || path[2]!='\\' || path.IndexOf(':',2)>=0 || path.IndexOf('/')>=0 ||
                !String.Equals(Path.GetFullPath(path),path,StringComparison.Ordinal))
                throw new ArgumentException("Exact local drive-rooted Transition path required.");
            string[] components=path.Substring(3).Split('\\');
            foreach(string component in components) {
                if(component.Length==0 || component.EndsWith(".",StringComparison.Ordinal) || component.EndsWith(" ",StringComparison.Ordinal) ||
                    component.IndexOfAny(Path.GetInvalidFileNameChars())>=0)
                    throw new ArgumentException("Transition path contains an ambiguous component.");
                string stem=component.Split('.')[0].ToUpperInvariant();
                if(stem=="CON" || stem=="PRN" || stem=="AUX" || stem=="NUL" ||
                    System.Text.RegularExpressions.Regex.IsMatch(stem,"\\A(?:COM|LPT)[1-9]\\z"))
                    throw new ArgumentException("Transition path contains a reserved device name.");
            }
            if(directory && !Directory.Exists(path)) throw new ArgumentException("Transition directory is missing.");
        }
        private static int BoundedInteger(string value,int minimum,int maximum) {
            int number;
            if(!Int32.TryParse(value,NumberStyles.None,CultureInfo.InvariantCulture,out number) ||
                number<minimum || number>maximum || number.ToString(CultureInfo.InvariantCulture)!=value)
                throw new ArgumentException("Exact bounded Transition integer required.");
            return number;
        }
        private static string EnvironmentBlock(IDictionary<string,string> environment) {
            if(environment==null) throw new ArgumentNullException("environment");
            SortedDictionary<string,string> copy=new SortedDictionary<string,string>(StringComparer.OrdinalIgnoreCase);
            HashSet<string> allowed=new HashSet<string>(new string[] {"SystemRoot","WINDIR","PATH","TEMP","TMP",
                "USERPROFILE","LOCALAPPDATA","APPDATA","ProgramData","ProgramFiles","ProgramFiles(x86)",
                "CommonProgramFiles","ComSpec","SystemDrive"},StringComparer.OrdinalIgnoreCase);
            foreach(KeyValuePair<string,string> item in environment) {
                SafeToken(item.Key); SafeToken(item.Value);
                if(!allowed.Contains(item.Key) || item.Key.IndexOf('=')>=0 || copy.ContainsKey(item.Key))
                    throw new ArgumentException("Unreviewed or aliased Transition environment key.");
                copy.Add(item.Key,item.Value);
            }
            if(!copy.ContainsKey("SystemRoot") || !copy.ContainsKey("WINDIR") || !copy.ContainsKey("PATH"))
                throw new ArgumentException("Explicit Windows root and PATH environment required.");
            StringBuilder block=new StringBuilder();
            foreach(KeyValuePair<string,string> item in copy) block.Append(item.Key).Append('=').Append(item.Value).Append('\0');
            block.Append('\0');
            if(block.Length>32767) throw new ArgumentException("Transition environment exceeds its fixed bound.");
            return block.ToString();
        }
        private static Dictionary<string,string> CopyPlan(IDictionary<string,string> plan) {
            if(plan==null || plan.Count!=PlanKeys.Length) throw new ArgumentException("Exact nineteen-key Transition plan required.");
            Dictionary<string,string> copy=new Dictionary<string,string>(StringComparer.Ordinal);
            foreach(KeyValuePair<string,string> item in plan) {
                SafeToken(item.Key); SafeToken(item.Value);
                if(Array.IndexOf(PlanKeys,item.Key)<0 || copy.ContainsKey(item.Key))
                    throw new ArgumentException("Unknown, duplicated or case-aliased Transition plan key.");
                copy.Add(item.Key,item.Value);
            }
            foreach(string name in PlanKeys) if(!copy.ContainsKey(name)) throw new ArgumentException("Missing Transition plan key.");
            if(!System.Text.RegularExpressions.Regex.IsMatch(copy["SessionId"],"\\A[0-9a-f]{32}\\z") ||
                copy["PostgresContainer"]!=RetainedPostgresId)
                throw new ArgumentException("Exact session and retained PostgreSQL identity required.");
            BoundedInteger(copy["OwnerPid"],1,Int32.MaxValue); BoundedInteger(copy["HeartbeatTimeoutSeconds"],1,60);
            CanonicalUtc(copy["OwnerStartTimeUtc"]); CanonicalUtc(copy["DeadlineUtc"]);
            HashSyntax(copy["ExpectedPreStartSha256"]); HashSyntax(copy["ExpectedStartFenceSha256"]);
            return copy;
        }
        private void Hold(string path,string hash,int maximum) {
            HashSyntax(hash); CanonicalPath(path,false); Files.RegularAncestry(path,true);
            FileStream stream=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.Read);
            try {
                if(stream.Length<1 || stream.Length>maximum) throw new InvalidDataException("Transition held file exceeds its byte bound.");
                Files.RequireSingleLink(stream);
                if(Files.HashStream(stream)!=hash) throw new InvalidDataException("Transition held source hash differs.");
                held.Add(new HeldFile {Stream=stream,Hash=hash,Maximum=maximum});
            } catch(Exception primary) {
                try { stream.Dispose(); } catch(Exception cleanup) { throw new AggregateException(primary,cleanup); }
                throw;
            }
        }
        public static IndependentTransitionLaunch Prepare(string executable,string executableHash,
                string source,string sourceHash,string runtime,string runtimeHash,string contract,string contractHash,
                IDictionary<string,string> plan,IDictionary<string,string> environment) {
            return new IndependentTransitionLaunch(executable,executableHash,source,sourceHash,runtime,runtimeHash,
                contract,contractHash,plan,environment);
        }
        private IndependentTransitionLaunch(string executable,string executableHash,string source,string sourceHash,
                string runtime,string runtimeHash,string contract,string contractHash,IDictionary<string,string> plan,
                IDictionary<string,string> environment) {
            // Managed-only preparation. No RNG, Files helper, filesystem probe,
            // handle, Job, pipe, thread or process exists before the caller roots this.
            launchPlan=CopyPlan(plan); root=launchPlan["Root"];
            CanonicalPath(root,false);
            foreach(string path in new string[] {executable,source,runtime,contract}) CanonicalPath(path,false);
            foreach(string name in new string[] {"LeasePath","ReceiptPath","HeartbeatPath","CommitPath","LiveAcceptancePath",
                "PreStartAttestationPath","StartFencePath","StartRevokePath","StartAttemptPath","StartCompletionPath"})
                CanonicalPath(launchPlan[name],false);
            HashSyntax(executableHash); HashSyntax(sourceHash); HashSyntax(runtimeHash); HashSyntax(contractHash);
            deadline=CanonicalUtc(launchPlan["DeadlineUtc"]); maximumLifetimeMilliseconds=(deadline-createdUtc).TotalMilliseconds;
            if(maximumLifetimeMilliseconds<=0 || maximumLifetimeMilliseconds>600000 ||
                CanonicalUtc(launchPlan["OwnerStartTimeUtc"])>createdUtc.AddSeconds(2))
                throw new ArgumentException("Transition original deadline/owner birth is outside the fixed bound.");
            this.executable=executable; this.source=source; this.runtime=runtime; this.contract=contract;
            this.executableHash=executableHash; this.sourceHash=sourceHash; this.runtimeHash=runtimeHash; this.contractHash=contractHash;
            leasePath=launchPlan["LeasePath"]; receiptPath=launchPlan["ReceiptPath"]; SessionId=launchPlan["SessionId"];
            environmentText=EnvironmentBlock(environment);
            commandSession=new CommandSessionOwner(this);
        }
        public void StartSuspended() {
            lock(lifecycleGate) {
                if(disposed || releaseStarted || retirementRequested || startAttempted)
                    throw new InvalidOperationException("Retained Transition native start is single-use and cannot follow cleanup.");
                startAttempted=true; startInProgress=true;
            try {
            Dictionary<string,string> copy=launchPlan;
            string root=copy["Root"];
            CanonicalPath(root,true);
            Files.RegularAncestry(root,true);
            if(!Directory.Exists(root) || root.EndsWith("\\",StringComparison.Ordinal))
                throw new ArgumentException("Exact Transition root directory required.");
            if(source!=Path.Combine(root,"scripts","Invoke-StratusSuccessorTransitionWatchdog.ps1") ||
                runtime!=Path.Combine(root,"scripts","Stratus.ParentStage.Runtime.cs"))
                throw new ArgumentException("Transition source/runtime paths are fixed beneath the accepted root.");
            HashSet<string> paths=new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach(string path in new string[] {executable,source,runtime,contract}) {
                CanonicalPath(path,false); Files.RegularAncestry(path,true);
                if(!paths.Add(path)) throw new ArgumentException("Transition held inputs alias.");
            }
            foreach(string name in new string[] {"LeasePath","ReceiptPath","HeartbeatPath","CommitPath","LiveAcceptancePath",
                    "PreStartAttestationPath","StartFencePath","StartRevokePath","StartAttemptPath","StartCompletionPath"}) {
                string path=copy[name];
                CanonicalPath(path,false);
                if(!path.StartsWith(root+"\\",StringComparison.OrdinalIgnoreCase) || !paths.Add(path))
                    throw new ArgumentException("Transition artifact escapes root or aliases another input.");
                Files.RegularAncestry(path,name=="PreStartAttestationPath" || name=="StartFencePath");
            }
            // Do not open or hold StartFencePath: early independent process capture
            // must not deadlock the later exclusive start/commit/fence protocol.
            foreach(string name in new string[] {"LeasePath","ReceiptPath"})
                if(File.Exists(copy[name]) || Directory.Exists(copy[name])) throw new ArgumentException("Transition publication already exists.");
            AssertClock(true); commandSession.Initialize();
            IntPtr commandAdmission=commandSession.DuplicateAdmissionForLaunch();
            long admissionNumber=commandAdmission.ToInt64();
            if(admissionNumber<=0 || admissionNumber==Int64.MaxValue)
                throw new InvalidDataException("Transition command admission handle has no supported canonical value.");
            byte[] nonceBytes=new byte[32]; using(RandomNumberGenerator random=RandomNumberGenerator.Create()) random.GetBytes(nonceBytes);
            nonce=BitConverter.ToString(nonceBytes).Replace("-","").ToLowerInvariant();
            List<string> arguments=new List<string>(new string[] {"-NoLogo","-NoProfile","-NonInteractive","-File",source});
            foreach(string name in PlanKeys) { arguments.Add("-"+name); arguments.Add(copy[name]); }
            arguments.Add("-ParentStageNonce"); arguments.Add(Nonce);
            arguments.Add("-ParentCommandSessionHandle"); arguments.Add(admissionNumber.ToString(CultureInfo.InvariantCulture));
            StringBuilder command=new StringBuilder(Quote(executable));
            foreach(string argument in arguments) command.Append(' ').Append(Quote(argument));
            if(command.Length>32766) throw new ArgumentException("Transition native command exceeds its fixed bound.");
            string env=environmentText;
            argvSha256=Files.Hash(Files.Utf8.GetBytes(command.ToString())); environmentSha256=Files.Hash(Encoding.Unicode.GetBytes(env));
            IntPtr attributes=IntPtr.Zero,inheritList=IntPtr.Zero,environmentMemory=IntPtr.Zero;
            bool initialized=false; Exception failure=null;
            try {
                Hold(executable,executableHash,134217728); Hold(source,sourceHash,2097152);
                Hold(runtime,runtimeHash,2097152); Hold(contract,contractHash,1048576); AssertClock(true);
                // These four exact read-only duplicates remain inherited by the
                // independent child even if its launching parent disappears.
                foreach(HeldFile file in held) {
                    // Root an outcome record BEFORE native duplication. Failed
                    // nonzero output is retained as ambiguous, never CloseHandle'd.
                    SourceDuplicateOutcome outcome=new SourceDuplicateOutcome(); sourceDuplicates.Add(outcome);
                    IntPtr duplicate=IntPtr.Zero; outcome.Attempted=true;
                    bool copied=DuplicateHandle(GetCurrentProcess(),file.Stream.SafeFileHandle.DangerousGetHandle(),GetCurrentProcess(),
                        out duplicate,0,true,2);
                    outcome.Raw=duplicate; outcome.Succeeded=copied; outcome.Returned=true;
                    Check(copied,"Duplicate Transition source read lock");
                    if(duplicate==IntPtr.Zero || duplicate==new IntPtr(-1))
                        throw new InvalidDataException("Transition source duplicate returned an invalid handle.");
                }
                output=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                error=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                input=new AnonymousPipeServerStream(PipeDirection.Out,HandleInheritability.Inheritable);
                List<IntPtr> handles=new List<IntPtr>();
                foreach(SourceDuplicateOutcome duplicate in sourceDuplicates) {
                    AssertSourceDuplicateOutcome(duplicate);
                    if(!duplicate.Succeeded || duplicate.Closed) throw new InvalidOperationException("Exact live source duplicate is missing.");
                    handles.Add(duplicate.Raw);
                }
                handles.Add(input.ClientSafePipeHandle.DangerousGetHandle()); handles.Add(output.ClientSafePipeHandle.DangerousGetHandle());
                handles.Add(error.ClientSafePipeHandle.DangerousGetHandle());
                handles.Add(commandAdmission);
                IntPtr size=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,1,0,ref size);
                if(size==IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(),"Transition startup attribute size");
                attributes=Marshal.AllocHGlobal(size); Check(InitializeProcThreadAttributeList(attributes,1,0,ref size),"Transition startup attributes");
                initialized=true; inheritList=Marshal.AllocHGlobal(IntPtr.Size*handles.Count);
                for(int i=0;i<handles.Count;i++) Marshal.WriteIntPtr(inheritList,i*IntPtr.Size,handles[i]);
                Check(UpdateProcThreadAttribute(attributes,0,new IntPtr(0x20002),inheritList,new IntPtr(IntPtr.Size*handles.Count),
                    IntPtr.Zero,IntPtr.Zero),"Exact Transition inherited handle allowlist");
                StartupEx startup=new StartupEx(); startup.startup.cb=Marshal.SizeOf(startup); startup.startup.flags=0x101;
                startup.startup.stdin=input.ClientSafePipeHandle.DangerousGetHandle(); startup.startup.stdout=output.ClientSafePipeHandle.DangerousGetHandle();
                startup.startup.stderr=error.ClientSafePipeHandle.DangerousGetHandle(); startup.attributes=attributes;
                environmentMemory=Marshal.StringToHGlobalUni(env); AssertClock(true);
                ProcessInfo created=new ProcessInfo();
                // NO_WINDOW | EXTENDED_STARTUPINFO | UNICODE_ENVIRONMENT | SUSPENDED.
                // No Job escape flag: inherited ANY-Job membership fails closed.
                createAttempted=true;
                bool createdOkay=CreateProcess(executable,command,IntPtr.Zero,IntPtr.Zero,true,0x08080404,environmentMemory,root,
                    ref startup,out created);
                // Record ALL raw outputs before checking success. An exception
                // or a false return with partial outputs is not a no-child fact.
                creationOutput=created; process=created.process; thread=created.thread; nativeProcessId=created.pid;
                createSucceeded=createdOkay; createReturned=true;
                Check(createdOkay,"Create Transition guard suspended");
                if(process==IntPtr.Zero || thread==IntPtr.Zero || created.pid<=0 || created.tid<=0)
                    throw new InvalidDataException("Transition creation returned incomplete original handles.");
                long born,exited,kernel,user; Check(GetProcessTimes(process,out born,out exited,out kernel,out user),"Transition process creation time");
                if(born<=0) throw new InvalidDataException("Transition native birth is invalid.");
                nativeStartedAt=new DateTimeOffset(DateTime.FromFileTimeUtc(born)).ToString("o");
                AssertIdentity(); identityEstablished=true; AssertNoJob(); AssertClock(true);
                commandSession.BindCreator(); commandSession.CloseLaunchCopy();
                CloseLocalClientEnds(); EnsureCollectors();
            } catch(Exception errorValue) { failure=errorValue; }
            finally {
                if(initialized) DeleteProcThreadAttributeList(attributes);
                if(attributes!=IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if(inheritList!=IntPtr.Zero) Marshal.FreeHGlobal(inheritList);
                if(environmentMemory!=IntPtr.Zero) Marshal.FreeHGlobal(environmentMemory);
                try { CloseSourceDuplicates(); } catch(Exception cleanup) { failure=Files.Combine(failure,cleanup); }
            }
            if(failure!=null) {
                throw failure;
            }
            AssertClock(true); ThrowFaults(); startCompleted=true;
            } catch(Exception failure) { Record(failure); throw; }
            finally { startInProgress=false; }
            }
        }
        private void CloseSourceDuplicates() {
            foreach(SourceDuplicateOutcome duplicate in sourceDuplicates) AssertSourceDuplicateOutcome(duplicate);
            Exception failure=null;
            foreach(SourceDuplicateOutcome duplicate in sourceDuplicates) {
                if(!duplicate.Succeeded || duplicate.Closed) continue;
                if(CloseHandle(duplicate.Raw)) duplicate.Closed=true;
                else failure=Files.Combine(failure,new Win32Exception(Marshal.GetLastWin32Error(),"Close parent Transition source duplicate"));
            }
            if(failure!=null) throw failure;
        }
        private static void AssertSourceDuplicateOutcome(SourceDuplicateOutcome duplicate) {
            if(duplicate==null || !duplicate.Attempted || !duplicate.Returned ||
                (duplicate.Succeeded && (duplicate.Raw==IntPtr.Zero || duplicate.Raw==new IntPtr(-1))) ||
                (!duplicate.Succeeded && duplicate.Raw!=IntPtr.Zero))
                throw new InvalidOperationException("Ambiguous original source duplication must remain retained without handle operations.");
        }
        private void CloseLocalClientEnds() {
            if(output!=null && !outputClientClosed) { output.DisposeLocalCopyOfClientHandle(); outputClientClosed=true; }
            if(error!=null && !errorClientClosed) { error.DisposeLocalCopyOfClientHandle(); errorClientClosed=true; }
            if(input!=null && !inputClientClosed) { input.DisposeLocalCopyOfClientHandle(); inputClientClosed=true; }
            if(input!=null) { input.Dispose(); input=null; }
        }
        private void EnsureCollectors() {
            if(output!=null) {
                if(outputThread==null) { outputThread=new Thread(delegate() { Collect(output,true); }); outputThread.IsBackground=true; }
                if((outputThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0) outputThread.Start();
            }
            if(error!=null) {
                if(errorThread==null) { errorThread=new Thread(delegate() { Collect(error,false); }); errorThread.IsBackground=true; }
                if((errorThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0) errorThread.Start();
            }
        }
        private void Record(Exception failure) { lock(captureGate) { if(faults.Count<8) faults.Add(failure); } }
        private void ThrowFaults() { lock(captureGate) { if(faults.Count!=0) throw new AggregateException(faults.ToArray()); } }
        private void AssertClock(bool readiness) {
            if(DateTimeOffset.UtcNow>=deadline || lifetimeClock.Elapsed.TotalMilliseconds>=maximumLifetimeMilliseconds ||
                (readiness && lifetimeClock.ElapsedMilliseconds>=30000))
                throw new TimeoutException("Transition original observation/readiness deadline expired.");
        }
        private void Collect(Stream pipe,bool stdout) {
            try {
                byte[] buffer=new byte[4096];
                while(true) {
                    int count=pipe.Read(buffer,0,buffer.Length);
                    lock(captureGate) {
                        if(count==0) { if(stdout) outputEof=true; else errorEof=true; return; }
                        int maximum=stdout ? OutputMaximumBytes : ErrorMaximumBytes;
                        long before=stdout ? outputCount : errorCount,after=Math.Min((long)maximum+1,before+count);
                        if(stdout) {
                            outputCount=after; int retained=(int)Math.Min(count,Math.Max(0,maximum-outputBytes.Length));
                            if(retained>0) outputBytes.Write(buffer,0,retained);
                        } else errorCount=after;
                        if(before<=maximum && after>maximum) Record(new InvalidDataException("Transition capture byte bound exceeded."));
                        if(!stdout && before==0) Record(new InvalidDataException("Transition guard wrote stderr."));
                    }
                    // Bounded recording, continued draining. Never kill or close
                    // the possibly last guard merely because capture failed.
                }
            } catch(Exception failure) { Record(failure); }
        }
        private bool IsAlive() {
            if(disposed) throw new ObjectDisposedException("IndependentTransitionLaunch");
            AssertOriginalCreationIdentity();
            uint wait=WaitForSingleObject(process,0);
            if(wait==0) return false; if(wait==258) return true;
            Check(wait!=UInt32.MaxValue,"Transition exact process wait");
            throw new InvalidOperationException("Unknown Transition process wait result.");
        }
        private void AssertNoJob() {
            AssertOriginalCreationIdentity();
            bool member; Check(IsProcessInJob(process,IntPtr.Zero,out member),"Transition ANY-Job absence");
            if(member) throw new InvalidOperationException("Transition safety guard belongs to a Job.");
        }
        private void AssertIdentity() {
            // The first identity establishment may follow only a recorded native
            // success with complete original outputs, never failed/partial data.
            if(disposed || !createAttempted || !createReturned || !createSucceeded ||
                process==IntPtr.Zero || thread==IntPtr.Zero || process!=creationOutput.process || thread!=creationOutput.thread ||
                nativeProcessId<=0 || nativeProcessId!=creationOutput.pid || creationOutput.tid<=0 || nativeStartedAt==null)
                throw new InvalidOperationException("Successful original Transition creation outputs are required for identity queries.");
            long born,exited,kernel,user; Check(GetProcessTimes(process,out born,out exited,out kernel,out user),"Transition held process birth");
            StringBuilder image=new StringBuilder(32768); uint size=(uint)image.Capacity;
            Check(QueryFullProcessImageName(process,0,image,ref size),"Transition held process image");
            if(GetProcessId(process)!=nativeProcessId || born<=0 || new DateTimeOffset(DateTime.FromFileTimeUtc(born)).ToString("o")!=nativeStartedAt ||
                !String.Equals(Path.GetFullPath(image.ToString()),executable,StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Transition exact process identity differs.");
            foreach(HeldFile file in held) {
                if(file.Stream.Length<1 || file.Stream.Length>file.Maximum) throw new InvalidDataException("Transition retained file bound differs.");
                Files.RequireSingleLink(file.Stream);
                if(Files.HashStream(file.Stream)!=file.Hash) throw new InvalidDataException("Transition retained source bytes changed.");
            }
        }
        private void AssertLive(bool readiness) {
            if(releaseStarted || retirementRequested) throw new InvalidOperationException("Transition cleanup forbids live protocol observations.");
            if(!resumeSucceeded) throw new InvalidOperationException("Transition has no successful owned Resume.");
            AssertClock(readiness); ThrowFaults();
            if(!IsAlive()) throw new InvalidOperationException("Transition guard is not live.");
            AssertIdentity(); AssertNoJob();
            if(!IsAlive()) throw new InvalidOperationException("Transition exited during live proof.");
            AssertClock(readiness); ThrowFaults();
        }
        public void AssertSuspended() {
            lock(lifecycleGate) {
            if(releaseStarted || retirementRequested) throw new InvalidOperationException("Transition cleanup forbids suspended admission.");
            if(resumeAttempted || !IsAlive()) throw new InvalidOperationException("Exact never-resumed Transition process required.");
            AssertIdentity(); AssertNoJob(); AssertClock(true);
            try {
                uint previous=SuspendThread(thread); Check(previous!=UInt32.MaxValue,"Transition suspended count");
                if(previous!=1) throw new InvalidOperationException("Transition suspension was disproved; retain the inspection increment.");
                uint restored=ResumeThread(thread); Check(restored!=UInt32.MaxValue,"Transition suspended count restoration");
                if(restored!=2) throw new InvalidOperationException("Transition suspension restoration was ambiguous.");
            } catch(Exception failure) { resumeAttempted=true; Record(failure); throw; }
            }
        }
        public void Resume() {
            lock(lifecycleGate) {
            if(!startCompleted || startInProgress || retirementRequested || releaseStarted)
                throw new InvalidOperationException("Transition Resume requires the completed original start before cleanup.");
            if(resumeAttempted) throw new InvalidOperationException("Transition Resume may be attempted only once.");
            commandSession.AssertCreatorBoundForResume();
            AssertSuspended(); ThrowFaults(); AssertClock(true); AssertNoJob(); AssertClock(true);
            resumeAttempted=true; // A possible Resume irreversibly forbids abort.
            try {
                uint previous=ResumeThread(thread); Check(previous!=UInt32.MaxValue,"Resume Transition guard");
                if(previous!=1) throw new InvalidOperationException("Transition Resume count differs.");
                resumeSucceeded=true;
            } catch(Exception failure) { Record(failure); throw; }
            }
        }
        // StrictJson verifies grammar/UTF8/duplicates first. This second parser
        // accepts only the exact ordered flat fields, compact punctuation and
        // native integer tokens; decoded strings retain arbitrary valid escapes.
        private static string ReadString(string text,ref int position) {
            if(position>=text.Length || text[position++]!='"') throw new InvalidDataException("Transition frame string required.");
            StringBuilder value=new StringBuilder();
            while(position<text.Length) {
                char c=text[position++]; if(c=='"') return value.ToString();
                if(c!='\\') { value.Append(c); continue; }
                if(position>=text.Length) throw new InvalidDataException("Incomplete Transition string escape.");
                c=text[position++];
                switch(c) {
                    case '"': case '\\': case '/': value.Append(c); break;
                    case 'b': value.Append('\b'); break; case 'f': value.Append('\f'); break;
                    case 'n': value.Append('\n'); break; case 'r': value.Append('\r'); break; case 't': value.Append('\t'); break;
                    case 'u':
                        if(position+4>text.Length) throw new InvalidDataException("Incomplete Transition unicode escape.");
                        value.Append((char)UInt16.Parse(text.Substring(position,4),NumberStyles.HexNumber,CultureInfo.InvariantCulture)); position+=4; break;
                    default: throw new InvalidDataException("Invalid Transition string escape.");
                }
            }
            throw new InvalidDataException("Unterminated Transition string.");
        }
        private static void Punctuation(string text,ref int position,char expected) {
            if(position>=text.Length || text[position++]!=expected) throw new InvalidDataException("Transition frame is not compact and ordered.");
        }
        private Dictionary<string,string> ParseFrame(byte[] bytes,bool complete) {
            int maximum=complete ? CompleteMaximumBytes : ReadyMaximumBytes;
            if(bytes==null || bytes.Length<3 || bytes.Length>maximum || bytes[bytes.Length-1]!=10)
                throw new InvalidDataException("Transition frame byte bound/newline differs.");
            for(int i=0;i<bytes.Length-1;i++) if(bytes[i]==0 || bytes[i]==10 || bytes[i]==13)
                throw new InvalidDataException("Transition frame contains a raw control or extra line.");
            StrictJson.Validate(bytes);
            string text=Files.Utf8.GetString(bytes); int position=0;
            string[] names=complete ? new string[] {"format","session_id","nonce","pid","started_at","receipt_path","receipt_sha256","receipt_bytes"}
                : new string[] {"format","session_id","nonce","pid","started_at","lease_path","lease_sha256"};
            Dictionary<string,string> values=new Dictionary<string,string>(StringComparer.Ordinal);
            Punctuation(text,ref position,'{');
            foreach(string name in names) {
                if(values.Count>0) Punctuation(text,ref position,',');
                if(ReadString(text,ref position)!=name) throw new InvalidDataException("Transition frame field order/set differs.");
                Punctuation(text,ref position,':');
                if(name=="pid" || name=="receipt_bytes") {
                    int start=position; while(position<text.Length && text[position]>='0' && text[position]<='9') position++;
                    string number=text.Substring(start,position-start); BoundedInteger(number,1,name=="pid" ? Int32.MaxValue : 262144); values.Add(name,number);
                } else values.Add(name,ReadString(text,ref position));
            }
            Punctuation(text,ref position,'}'); Punctuation(text,ref position,'\n');
            if(position!=text.Length || values["format"]!=(complete ? "stratus-parent-transition-complete-v1" : "stratus-parent-transition-ready-v1") ||
                values["session_id"]!=SessionId || values["nonce"]!=Nonce || values["pid"]!=ProcessId.ToString(CultureInfo.InvariantCulture) ||
                values["started_at"]!=StartedAt || values[complete ? "receipt_path" : "lease_path"]!=(complete ? receiptPath : leasePath))
                throw new InvalidDataException("Transition frame differs from the original private launch identity.");
            CanonicalUtc(values["started_at"]); HashSyntax(values[complete ? "receipt_sha256" : "lease_sha256"]);
            return values;
        }
        private byte[] ReadFrameLocked(int start,int maximum) {
            if(faults.Count!=0) throw new AggregateException(faults.ToArray());
            if(errorCount!=0 || outputCount!=outputBytes.Length) throw new InvalidDataException("Transition stream capture is not clean and complete.");
            byte[] buffer=outputBytes.GetBuffer(); int available=(int)outputBytes.Length-start;
            if(available<0) throw new InvalidDataException("Transition captured prefix was lost.");
            for(int i=0;i<Math.Min(available,maximum);i++) if(buffer[start+i]==10) {
                byte[] frame=new byte[i+1]; Buffer.BlockCopy(buffer,start,frame,0,frame.Length); return frame;
            }
            if(available>=maximum) throw new InvalidDataException("Transition frame exceeds its fixed limit.");
            return null;
        }
        private void AssertReadyBytesLocked() {
            if(readyCapture==null || outputCount!=readyCapture.Length || outputBytes.Length!=readyCapture.Length ||
                errorCount!=0 || outputEof || errorEof || Files.Hash(outputBytes.ToArray())!=Files.Hash(readyCapture))
                throw new InvalidDataException("Transition live READY capture changed or reached EOF.");
            if(faults.Count!=0) throw new AggregateException(faults.ToArray());
        }
        public ReadyProof PollReady() {
            try {
                AssertLive(readyProof==null);
                if(readyProof!=null) { AssertReadyLive(); return readyProof; }
                byte[] frame; Dictionary<string,string> parsed;
                lock(captureGate) {
                    if(outputEof || errorEof) throw new EndOfStreamException("Transition READY pipe reached EOF.");
                    frame=ReadFrameLocked(0,ReadyMaximumBytes);
                    if(frame==null) { AssertClock(true); return null; }
                    if(outputCount!=frame.Length) throw new InvalidDataException("Transition READY must be the sole captured frame.");
                    parsed=ParseFrame(frame,false);
                }
                AssertLive(true);
                lock(captureGate) {
                    if(outputCount!=frame.Length || Files.Hash(outputBytes.ToArray())!=Files.Hash(frame) || outputEof || errorEof)
                        throw new InvalidDataException("Transition READY changed during live verification.");
                    if(!IsAlive()) throw new InvalidOperationException("Transition exited before READY capability publication.");
                    AssertClock(true); ThrowFaults();
                    readyCapture=frame; readyLeaseHash=parsed["lease_sha256"]; readyProofReady=true;
                    readyProof=ReadyProof.FromOwned(this); return readyProof;
                }
            } catch(Exception failure) { Record(failure); throw; }
        }
        public void AssertReadyLive() {
            try {
                if(readyProof==null) throw new InvalidOperationException("Owned Transition READY capability is missing.");
                AssertLive(false);
                lock(captureGate) { AssertReadyBytesLocked(); }
                AssertLive(false);
            } catch(Exception failure) { Record(failure); throw; }
        }
        public TerminalExitProof PollTerminalExit() {
            try {
                if(releaseStarted || retirementRequested) throw new InvalidOperationException("Transition cleanup cannot publish protocol success.");
                if(!resumeSucceeded || readyProof==null) throw new InvalidOperationException("Transition terminal observation requires accepted owned READY.");
                AssertClock(false); AssertIdentity(); AssertNoJob(); ThrowFaults();
                bool alive=IsAlive(); AssertClock(false); ThrowFaults();
                byte[] complete;
                lock(captureGate) {
                    byte[] first=ReadFrameLocked(0,ReadyMaximumBytes);
                    if(first==null || Files.Hash(first)!=readyProof.Sha256) throw new InvalidDataException("Transition original READY prefix differs.");
                    complete=ReadFrameLocked(first.Length,CompleteMaximumBytes);
                    if(complete==null) {
                        if(outputEof) throw new EndOfStreamException("Transition COMPLETE frame is missing.");
                        return null;
                    }
                    if(outputCount!=first.Length+complete.Length) throw new InvalidDataException("Unexpected Transition trailing output.");
                    Dictionary<string,string> parsed=ParseFrame(complete,true);
                    if(completeCapture==null) {
                        completeCapture=complete; completeReceiptHash=parsed["receipt_sha256"];
                        completeReceiptBytes=BoundedInteger(parsed["receipt_bytes"],1,262144);
                    } else if(Files.Hash(completeCapture)!=Files.Hash(complete)) throw new InvalidDataException("Transition terminal frame changed.");
                    if(alive) { AssertClock(false); return null; }
                    if(!outputEof || !errorEof) { AssertClock(false); return null; }
                }
                if(!outputThread.Join(0) || !errorThread.Join(0)) { AssertClock(false); return null; }
                uint code; Check(GetExitCodeProcess(process,out code),"Transition terminal exit code");
                long born,exited,kernel,user; Check(GetProcessTimes(process,out born,out exited,out kernel,out user),"Transition terminal native times");
                if(code!=0 || exited<=0 || exited<born || new DateTimeOffset(DateTime.FromFileTimeUtc(born)).ToString("o")!=StartedAt)
                    throw new InvalidDataException("Transition terminal exit/birth differs.");
                string exitedAt=new DateTimeOffset(DateTime.FromFileTimeUtc(exited)).ToString("o");
                AssertIdentity(); AssertNoJob(); AssertClock(false); ThrowFaults();
                if(IsAlive()) throw new InvalidOperationException("Transition exact terminal process became live.");
                AssertClock(false); ThrowFaults();
                lock(captureGate) {
                    if(!outputEof || !errorEof || errorCount!=0 || outputCount!=readyCapture.Length+completeCapture.Length)
                        throw new InvalidDataException("Transition terminal EOF/capture proof differs.");
                    AssertClock(false); ThrowFaults();
                    if(terminalProof!=null) {
                        if(terminalProof.ExitedAt!=exitedAt) throw new InvalidDataException("Transition terminal exit time changed.");
                        return terminalProof;
                    }
                    terminalExitedAt=exitedAt; terminalStdoutHash=Files.Hash(outputBytes.ToArray()); terminalProofReady=true;
                    terminalProof=TerminalExitProof.FromOwned(this); return terminalProof;
                }
            } catch(Exception failure) { Record(failure); throw; }
        }
        internal enum CommandCreatorDisposition { NeverCreated, RetainedOriginal }
        private void AssertOriginalCreationIdentity() {
            if(disposed || !createAttempted || !createReturned || !createSucceeded || !identityEstablished ||
                process==IntPtr.Zero || thread==IntPtr.Zero || process!=creationOutput.process || thread!=creationOutput.thread ||
                nativeProcessId<=0 || nativeProcessId!=creationOutput.pid || creationOutput.tid<=0 || nativeStartedAt==null)
                throw new InvalidOperationException("Unambiguous original Transition creation and native identity must remain retained.");
        }
        internal CommandCreatorDisposition GetCommandCreatorDisposition() {
            lock(lifecycleGate) {
                if(disposed || startInProgress) throw new InvalidOperationException("Transition creation is still active or released.");
                bool zeroOutputs=creationOutput.process==IntPtr.Zero && creationOutput.thread==IntPtr.Zero &&
                    creationOutput.pid==0 && creationOutput.tid==0 && process==IntPtr.Zero && thread==IntPtr.Zero;
                if(!resumeAttempted && zeroOutputs && (!createAttempted || (createReturned && !createSucceeded)))
                    return CommandCreatorDisposition.NeverCreated;
                if(createAttempted && createReturned && createSucceeded && identityEstablished &&
                    process!=IntPtr.Zero && thread!=IntPtr.Zero && process==creationOutput.process && thread==creationOutput.thread &&
                    nativeProcessId==creationOutput.pid && nativeProcessId>0 && creationOutput.tid>0)
                    return CommandCreatorDisposition.RetainedOriginal;
                throw new InvalidOperationException("Ambiguous Transition creation or missing native identity must remain retained.");
            }
        }
        public void AssertNoChildCreated() {
            if(GetCommandCreatorDisposition()!=CommandCreatorDisposition.NeverCreated)
                throw new InvalidOperationException("Original Transition capability does not prove that no child was created.");
        }
        public bool PollCommandSessionRetirement() {
            lock(lifecycleGate) {
                if(disposed || startInProgress) throw new InvalidOperationException("Transition native start is active or released.");
                retirementRequested=true;
                if(commandSessionRetired) return true;
                bool retired=commandSession.PollRetirement();
                if(retired) commandSessionRetired=true;
                return retired;
            }
        }
        // Fresh physical creator death for the internal command-session owner.
        // No cached RootTerminationObservation/receipt or caller retirement flag
        // is accepted, and this says nothing about Docker's queued work.
        internal void AssertCommandCreatorTerminated() {
            AssertOriginalCreationIdentity();
            if(disposed || process==IntPtr.Zero || !identityEstablished)
                throw new InvalidOperationException("Original command creator capability is unavailable.");
            IntPtr original=process;
            uint wait=WaitForSingleObject(original,0);
            Check(wait!=UInt32.MaxValue,"Command creator native wait");
            if(wait!=0) throw new InvalidOperationException("Original command creator is not physically terminated.");
            long born,exited,kernel,user;
            Check(GetProcessTimes(original,out born,out exited,out kernel,out user),"Command creator native times");
            if(GetProcessId(original)!=(uint)ProcessId || born<=0 || exited<born || exited<=0 ||
                new DateTimeOffset(DateTime.FromFileTimeUtc(born)).ToString("o")!=StartedAt)
                throw new InvalidDataException("Command creator native identity differs.");
            uint after=WaitForSingleObject(original,0);
            Check(after!=UInt32.MaxValue,"Command creator native death recheck");
            if(disposed || process!=original || after!=0)
                throw new InvalidOperationException("Original command creator handle/death was not retained.");
        }
        // Do not reuse AssertIdentity/AssertClock/ThrowFaults here: those also
        // enforce protocol/source acceptance, which may already have failed.
        // Exact native root identity still cannot be replaced by a PID lookup,
        // receipt, EOF, expired deadline or a prior error.
        public RootTerminationObservation PollRootTermination() {
            try {
                AssertOriginalCreationIdentity();
                if(disposed || process==IntPtr.Zero || !identityEstablished)
                    throw new InvalidOperationException("Retained original Transition process identity is required.");
                if(rootTerminationObservation!=null) return rootTerminationObservation;
                IntPtr heldProcess=process;
                uint wait=WaitForSingleObject(heldProcess,0);
                if(wait==258) return null;
                Check(wait!=UInt32.MaxValue,"Transition root-death wait");
                if(wait!=0) throw new InvalidOperationException("Unknown Transition root-death wait result.");
                uint observedPid=GetProcessId(heldProcess); Check(observedPid!=0,"Transition root-death PID");
                long born,exited,kernel,user;
                Check(GetProcessTimes(heldProcess,out born,out exited,out kernel,out user),"Transition root-death native times");
                uint code; Check(GetExitCodeProcess(heldProcess,out code),"Transition root-death native exit code");
                if(observedPid!=(uint)ProcessId || born<=0 || exited<born || exited<=0 ||
                    new DateTimeOffset(DateTime.FromFileTimeUtc(born)).ToString("o")!=StartedAt)
                    throw new InvalidDataException("Transition root-death identity or native times differ.");
                // A signaled process may legitimately have any DWORD exit code,
                // including 259 or an exception value. It is never a success test.
                uint after=WaitForSingleObject(heldProcess,0);
                Check(after!=UInt32.MaxValue,"Transition root-death recheck");
                if(after!=0) throw new InvalidOperationException("Transition root-death signal was not retained.");
                long bornAgain,exitedAgain,kernelAgain,userAgain; uint codeAgain;
                Check(GetProcessTimes(heldProcess,out bornAgain,out exitedAgain,out kernelAgain,out userAgain),"Transition root-death times recheck");
                Check(GetExitCodeProcess(heldProcess,out codeAgain),"Transition root-death exit recheck");
                if(bornAgain!=born || exitedAgain!=exited || codeAgain!=code)
                    throw new InvalidDataException("Transition root-death native facts changed.");
                bool stdoutCompleted=outputThread!=null && outputThread.Join(0);
                bool stderrCompleted=errorThread!=null && errorThread.Join(0);
                lock(captureGate) {
                    if(disposed || process!=heldProcess || !identityEstablished)
                        throw new InvalidOperationException("Transition root-death lost its exact retained handle.");
                    if(rootTerminationObservation!=null) return rootTerminationObservation;
                    DateTimeOffset observedAt=DateTimeOffset.UtcNow;
                    long elapsed=lifetimeClock.ElapsedMilliseconds;
                    rootTerminationFacts=new RootTerminationFacts { ExitCode=code,
                        ExitedAt=new DateTimeOffset(DateTime.FromFileTimeUtc(exited)).ToString("o"),
                        ObservedAt=observedAt.ToString("o"), ObservedElapsedMilliseconds=elapsed,
                        ObservedAfterProtocolDeadline=observedAt>=deadline || elapsed>=maximumLifetimeMilliseconds,
                        StdoutCollectorCompleted=stdoutCompleted, StderrCollectorCompleted=stderrCompleted };
                    rootTerminationObservation=RootTerminationObservation.FromOwned(this);
                    return rootTerminationObservation;
                }
            } catch(Exception failure) { Record(failure); throw; }
        }
        public void AbortBeforeResume() {
            lock(lifecycleGate) {
            if(startInProgress) throw new InvalidOperationException("Transition native start is still in progress.");
            if(resumeAttempted) throw new InvalidOperationException("Any Transition Resume uncertainty permanently forbids abort.");
            // C could close only after the original no-child/death boundary.
            // A later partial resource-close failure may leave only one original
            // handle. Retrying cleanup must not query that incomplete pair or
            // issue another abort; it must be able to reach retryable Dispose.
            if(commandSessionDisposed && releaseStarted) return;
            CommandCreatorDisposition disposition=GetCommandCreatorDisposition();
            if(disposition==CommandCreatorDisposition.NeverCreated) { AssertNoChildCreated(); return; }
            AssertOriginalCreationIdentity();
            if(!IsAlive()) return;
            AssertIdentity();
            uint previous=SuspendThread(thread);
            if(previous==UInt32.MaxValue) {
                resumeAttempted=true; Exception failure=new Win32Exception(Marshal.GetLastWin32Error(),"Transition abort suspended proof"); Record(failure); throw failure;
            }
            if(previous!=1) {
                // Do not re-enable a thread which this failed proof just paused.
                resumeAttempted=true;
                Exception failure=new InvalidOperationException("Transition abort lacks exact never-resumed suspended proof.");
                Record(failure); throw failure;
            }
            try {
                // Keep the inspection increment through exact termination: never
                // open a running window between suspended proof and abort.
                Check(TerminateProcess(process,202),"Abort exact never-resumed Transition guard");
                uint wait=WaitForSingleObject(process,5000); Check(wait!=UInt32.MaxValue,"Transition pre-resume abort wait");
                if(wait!=0) throw new TimeoutException("Transition pre-resume abort was not proved.");
            } catch(Exception failure) { resumeAttempted=true; Record(failure); throw; }
            }
        }
        public void Dispose() {
            // Public launch mutations use this reentrant gate. Internal owner
            // callbacks occur only while this same launch gate is held; they do
            // not acquire an independent nonreentrant operation latch.
            lock(lifecycleGate) {
            if(disposed) return;
            if(startInProgress) throw new InvalidOperationException("Active Transition native start cannot be disposed.");
            if(resumeAttempted && terminalProof==null)
                throw new InvalidOperationException("Ambiguous/resumed Transition capture must remain retained until qualified terminal observation.");
            releaseStarted=true;
            if(!commandSessionDisposed) {
                // Classify before ANY handle query: failed CreateProcess output
                // may be nonzero but must not become a Wait/Suspend/Kill target.
                CommandCreatorDisposition disposition=GetCommandCreatorDisposition();
                if(disposition==CommandCreatorDisposition.NeverCreated) AssertNoChildCreated();
                else if(IsAlive()) {
                    if(resumeAttempted) throw new InvalidOperationException("Live Transition guard cannot be disposed.");
                    AbortBeforeResume();
                }
                if(!PollCommandSessionRetirement()) throw new InvalidOperationException("Original command session remains active; creator capability retained.");
                // C closes before ANY source/pipe/original creator capability.
                // Failure here leaves those original handles available to retry.
                commandSession.Dispose(); commandSessionDisposed=true;
            }
            CloseLocalClientEnds(); EnsureCollectors();
            if(outputThread!=null && (outputThread.ThreadState & System.Threading.ThreadState.Unstarted)==0 && !outputThread.Join(2000))
                throw new TimeoutException("Transition stdout EOF cleanup is unproved; capability retained.");
            if(errorThread!=null && (errorThread.ThreadState & System.Threading.ThreadState.Unstarted)==0 && !errorThread.Join(2000))
                throw new TimeoutException("Transition stderr EOF cleanup is unproved; capability retained.");
            lock(captureGate) {
                if((output!=null && !outputEof) || (error!=null && !errorEof))
                    throw new InvalidOperationException("Transition actual pipe EOF remains unproved; capability retained.");
            }
            CloseSourceDuplicates();
            Exception failure=null;
            foreach(IDisposable item in new IDisposable[] {input,output,error,outputBytes}) if(item!=null)
                try { item.Dispose(); } catch(Exception cleanup) { failure=Files.Combine(failure,cleanup); }
            foreach(HeldFile file in held) try { file.Stream.Dispose(); } catch(Exception cleanup) { failure=Files.Combine(failure,cleanup); }
            if(thread!=IntPtr.Zero) { if(CloseHandle(thread)) thread=IntPtr.Zero; else failure=Files.Combine(failure,new Win32Exception(Marshal.GetLastWin32Error(),"Close Transition thread")); }
            if(process!=IntPtr.Zero) { if(CloseHandle(process)) process=IntPtr.Zero; else failure=Files.Combine(failure,new Win32Exception(Marshal.GetLastWin32Error(),"Close Transition process")); }
            if(failure!=null) throw failure;
            disposed=true;
            }
        }
    }

    // Private, bounded control transport. These are recent observations, not
    // transferred process handles or independent mutation/retirement authority.
    internal static class ControlWire {
        internal const int FrameMaximum=8192, PayloadMaximum=4096, RequestMaximum=1024;
        internal static void Hex(string value,int length) {
            if(value==null || !System.Text.RegularExpressions.Regex.IsMatch(value,"\\A[0-9a-f]{"+length+"}\\z"))
                throw new InvalidDataException("Private control identity is malformed.");
        }
        internal static byte[] Payload(byte[] bytes) {
            if(bytes==null || bytes.Length<2 || bytes.Length>PayloadMaximum) throw new InvalidDataException("Private control payload byte bound differs.");
            byte[] copy=(byte[])bytes.Clone(); StrictJson.Validate(copy); return copy;
        }
        internal static byte[] Request(string session,int sequence,string challenge,byte[] payload) {
            Hex(session,32); Hex(challenge,64);
            if(sequence<1 || sequence>RequestMaximum) throw new InvalidDataException("Private control sequence bound differs.");
            return Encode("STRATUS-CONTROL-REQUEST/1|"+session+"|"+sequence.ToString(CultureInfo.InvariantCulture)+"|"+challenge+"|"+Convert.ToBase64String(Payload(payload))+"\n");
        }
        internal static byte[] Response(string session,int sequence,string challenge,string requestHash,byte[] payload) {
            Hex(session,32); Hex(challenge,64); Hex(requestHash,64);
            if(sequence<1 || sequence>RequestMaximum) throw new InvalidDataException("Private control sequence bound differs.");
            return Encode("STRATUS-CONTROL-RESPONSE/1|"+session+"|"+sequence.ToString(CultureInfo.InvariantCulture)+"|"+challenge+"|"+requestHash+"|"+Convert.ToBase64String(Payload(payload))+"\n");
        }
        private static byte[] Encode(string text) {
            byte[] bytes=Files.Utf8.GetBytes(text);
            if(bytes.Length>FrameMaximum) throw new InvalidDataException("Private control frame byte bound differs.");
            return bytes;
        }
        internal static string[] Decode(byte[] frame,bool response,string session,out int sequence,out byte[] payload) {
            if(frame==null || frame.Length<2 || frame.Length>FrameMaximum || frame[frame.Length-1]!=10)
                throw new InvalidDataException("Private control frame is incomplete or oversized.");
            for(int i=0;i<frame.Length-1;i++) if(frame[i]<32 || frame[i]>126) throw new InvalidDataException("Private control envelope must be exact ASCII.");
            string text=Files.Utf8.GetString(frame); string[] parts=text.Substring(0,text.Length-1).Split('|');
            if(parts.Length!=(response ? 6 : 5) || parts[0]!=(response ? "STRATUS-CONTROL-RESPONSE/1" : "STRATUS-CONTROL-REQUEST/1") || parts[1]!=session)
                throw new InvalidDataException("Private control frame role/session differs.");
            Hex(parts[1],32); Hex(parts[3],64);
            if(!Int32.TryParse(parts[2],NumberStyles.None,CultureInfo.InvariantCulture,out sequence) || sequence<1 ||
                sequence>RequestMaximum || sequence.ToString(CultureInfo.InvariantCulture)!=parts[2]) throw new InvalidDataException("Private control frame sequence differs.");
            if(response) Hex(parts[4],64);
            string encoded=parts[response ? 5 : 4]; payload=Convert.FromBase64String(encoded);
            if(Convert.ToBase64String(payload)!=encoded) throw new InvalidDataException("Private control payload encoding is not canonical.");
            payload=Payload(payload); return parts;
        }
    }

    internal sealed class BoundedControlIo : IDisposable {
        private readonly Stream input,output;
        private readonly object sync=new object();
        private readonly List<Exception> faults=new List<Exception>();
        private Thread reader,writer;
        private byte[] received,pendingWrite;
        private int readUsed;
        private bool writeBusy,stopping,started,disposed,eof;
        private bool drainRequested,drainStarted,closeFirst,closeWritesRequested,writeClosed,drainComplete;
        internal BoundedControlIo(Stream input,Stream output) { this.input=input; this.output=output; }
        internal void Start() {
            lock(sync) {
                if(started || disposed) throw new InvalidOperationException("Private control IO start is single-use.");
                started=true; reader=new Thread(ReadLoop); writer=new Thread(WriteLoop);
                reader.IsBackground=true; writer.IsBackground=true; reader.Start(); writer.Start();
            }
        }
        internal void Fail(Exception error) { lock(sync) { if(faults.Count<8) faults.Add(error); Monitor.PulseAll(sync); } }
        private void CheckLocked() {
            if(disposed || stopping || eof || drainRequested) throw new InvalidOperationException("Private control channel closed, draining or reached EOF.");
            if(faults.Count!=0) throw new AggregateException(faults.ToArray());
        }
        private void CheckDrainLocked() {
            if(disposed || stopping || !started || !drainRequested)
                throw new InvalidOperationException("Private control terminal drain is unavailable.");
            if(faults.Count!=0) throw new AggregateException(faults.ToArray());
            if(received!=null || readUsed!=0) throw new InvalidDataException("Private control drain cannot discard received or partial bytes.");
        }
        internal void BeginDrain(bool closeFirst) {
            try { lock(sync) {
                if(drainRequested) throw new InvalidOperationException("Private control drain admission is single-use.");
                drainRequested=true; this.closeFirst=closeFirst;
                CheckDrainLocked();
                if(eof) throw new EndOfStreamException("Pre-terminal private control EOF cannot become successful drain.");
                // An already queued final write must actually finish/flush. No
                // new write can enter, and PollDrain owns the bounded wait.
                Monitor.PulseAll(sync);
            } } catch(Exception error) { Fail(error); throw; }
        }
        internal bool PollDrain() {
            try {
            lock(sync) {
                CheckDrainLocked();
                if(!drainStarted) {
                    if(pendingWrite!=null || writeBusy) return false;
                    drainStarted=true;
                }
                if(!closeWritesRequested && (closeFirst || eof)) {
                    closeWritesRequested=true; Monitor.PulseAll(sync);
                }
                if(!eof || !writeClosed) return false;
            }
            // These joins observe termination only; all pipe IO/close remains
            // on the dedicated workers, never on the supervising thread.
            if(reader==null || writer==null || !reader.Join(0) || !writer.Join(0)) return false;
            lock(sync) {
                CheckDrainLocked();
                if(!eof || !writeClosed || pendingWrite!=null || writeBusy) return false;
                drainComplete=true; return true;
            }
            } catch(Exception error) { Fail(error); throw; }
        }
        internal void AssertHealthy() { lock(sync) { CheckLocked(); } }
        internal byte[] Take() {
            lock(sync) {
                CheckLocked();
                if(received!=null && readUsed!=0) throw new InvalidDataException("Trailing partial private control frame.");
                byte[] frame=received; received=null; return frame;
            }
        }
        internal void AssertNoTrailing() {
            lock(sync) { CheckLocked(); if(received!=null || readUsed!=0) throw new InvalidDataException("Unexpected queued or partial private control bytes."); }
        }
        internal void Enqueue(byte[] frame) {
            if(frame==null || frame.Length<2 || frame.Length>ControlWire.FrameMaximum) throw new InvalidDataException("Private control write bound differs.");
            lock(sync) {
                CheckLocked(); if(!started || pendingWrite!=null || writeBusy) throw new InvalidOperationException("Private control allows one queued/in-flight write.");
                pendingWrite=(byte[])frame.Clone(); Monitor.PulseAll(sync);
            }
        }
        private void ReadLoop() {
            try {
                byte[] frame=new byte[ControlWire.FrameMaximum];
                while(true) {
                    int value=input.ReadByte();
                    lock(sync) {
                        if(value<0) { eof=true; if(readUsed!=0) Fail(new EndOfStreamException("Truncated private control frame.")); return; }
                        if(stopping) return;
                        if(drainRequested) throw new InvalidDataException("Private control received bytes after terminal drain began.");
                        if(readUsed>=frame.Length) throw new InvalidDataException("Private control frame exceeded its fixed bound.");
                        frame[readUsed++]=(byte)value;
                        if(value==10) {
                            if(received!=null) throw new InvalidDataException("Multiple unconsumed private control frames.");
                            received=new byte[readUsed]; Buffer.BlockCopy(frame,0,received,0,readUsed); readUsed=0;
                        }
                    }
                }
            } catch(Exception error) { Fail(error); }
        }
        private void WriteLoop() {
            try {
                while(true) {
                    byte[] frame; bool close=false;
                    lock(sync) {
                        while(pendingWrite==null && !closeWritesRequested && !stopping && faults.Count==0) Monitor.Wait(sync);
                        if(stopping || faults.Count!=0) return;
                        if(closeWritesRequested) {
                            if(pendingWrite!=null || writeBusy || !drainStarted) throw new InvalidOperationException("Private control write close lacks an idle drain boundary.");
                            frame=null; close=true;
                        } else { frame=pendingWrite; pendingWrite=null; writeBusy=true; }
                    }
                    if(close) {
                        output.Flush(); output.Dispose();
                        lock(sync) { writeClosed=true; Monitor.PulseAll(sync); }
                        return;
                    }
                    output.Write(frame,0,frame.Length); output.Flush();
                    lock(sync) { writeBusy=false; Monitor.PulseAll(sync); }
                }
            } catch(Exception error) { Fail(error); }
        }
        public void Dispose() {
            if(disposed) return;
            lock(sync) {
                if(drainRequested && !drainComplete) throw new InvalidOperationException("Unproved private control drain retains IO ownership.");
                stopping=true; Monitor.PulseAll(sync);
            }
            // No active guard may be retired to unblock these threads. Parent
            // launch disposal already requires its exact allowed terminal state.
            foreach(Thread thread in new Thread[] {reader,writer}) {
                if(thread!=null && (thread.ThreadState & System.Threading.ThreadState.Unstarted)==0 && !thread.Join(2000))
                    throw new TimeoutException("Private control IO shutdown is unproved; handles retained.");
            }
            Exception failure=null;
            foreach(Stream stream in new Stream[] {input,output}) if(stream!=null) try { stream.Dispose(); } catch(Exception error) { failure=Files.Combine(failure,error); }
            if(failure!=null) throw failure; disposed=true;
        }
    }

    public sealed class PrivateControlResponse {
        private readonly PrivateControlClient client;
        public readonly string PayloadText,RequestSha256,RequestPayloadSha256,Challenge;
        public readonly int Sequence;
        public readonly long ObservedRoundtripMilliseconds;
        internal PrivateControlResponse(PrivateControlClient client,string payload,string requestHash,string payloadHash,string challenge,int sequence,long elapsed) {
            this.client=client; PayloadText=payload; RequestSha256=requestHash; RequestPayloadSha256=payloadHash;
            Challenge=challenge; Sequence=sequence; ObservedRoundtripMilliseconds=elapsed;
        }
        public void Consume() { client.Consume(this); }
    }

    internal sealed class PrivateControlClient : IDisposable {
        private const int RoundtripMilliseconds=1000; // proposed bound, native qualification still required
        private readonly string session;
        private AnonymousPipeServerStream toChild,fromChild;
        private BoundedControlIo io;
        private IndependentWatchdogLaunch owner;
        private Stopwatch requestClock;
        private string challenge,requestHash,requestPayloadHash;
        private int sequence;
        private bool consumed=true,disposed,terminal;
        private PrivateControlResponse response;
        private int operation;
        internal PrivateControlClient(string session) {
            ControlWire.Hex(session,32); this.session=session;
            try {
                toChild=new AnonymousPipeServerStream(PipeDirection.Out,HandleInheritability.Inheritable);
                fromChild=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                io=new BoundedControlIo(fromChild,toChild);
            } catch { if(toChild!=null) toChild.Dispose(); if(fromChild!=null) fromChild.Dispose(); throw; }
        }
        internal IntPtr ChildInputHandle { get { return toChild.ClientSafePipeHandle.DangerousGetHandle(); } }
        internal IntPtr ChildOutputHandle { get { return fromChild.ClientSafePipeHandle.DangerousGetHandle(); } }
        internal string ChildInputHandleText { get { return toChild.GetClientHandleAsString(); } }
        internal string ChildOutputHandleText { get { return fromChild.GetClientHandleAsString(); } }
        internal void StartAfterInheritance() {
            toChild.DisposeLocalCopyOfClientHandle(); fromChild.DisposeLocalCopyOfClientHandle(); io.Start();
        }
        private void EnterOperation() {
            if(Interlocked.CompareExchange(ref operation,1,0)!=0) {
                Exception error=new InvalidOperationException("Concurrent private control client operation is forbidden.");
                io.Fail(error); throw error;
            }
        }
        private void ExitOperation() { Interlocked.Exchange(ref operation,0); }
        private void CheckHealth() {
            if(disposed) throw new ObjectDisposedException("PrivateControlClient");
            if(terminal) throw new InvalidOperationException("Private control live operations are unavailable in terminal mode.");
            io.AssertHealthy(); if(consumed) io.AssertNoTrailing();
        }
        internal void AssertHealthy() {
            EnterOperation();
            try { CheckHealth(); } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        private void RequireOwnerReady() {
            if(owner==null || owner.PollReady()==null) throw new InvalidOperationException("Private control requires its original accepted parent READY.");
        }
        private void Deadline() {
            CheckHealth(); if(requestClock==null || requestClock.ElapsedMilliseconds>=RoundtripMilliseconds)
                throw new TimeoutException("Private control original 1000ms roundtrip expired.");
        }
        internal void Begin(IndependentWatchdogLaunch owner,byte[] payload) {
            EnterOperation();
            try {
                if(!consumed || sequence>=ControlWire.RequestMaximum || (this.owner!=null && !Object.ReferenceEquals(this.owner,owner)))
                    throw new InvalidOperationException("Private control request is concurrent, replayed or over limit.");
                requestClock=Stopwatch.StartNew(); CheckHealth();
                consumed=false; response=null; this.owner=owner;
                RequireOwnerReady(); Deadline();
                byte[] body=ControlWire.Payload(payload), randomBytes=new byte[32];
                using(RandomNumberGenerator random=RandomNumberGenerator.Create()) random.GetBytes(randomBytes);
                challenge=BitConverter.ToString(randomBytes).Replace("-","").ToLowerInvariant(); sequence++;
                byte[] frame=ControlWire.Request(session,sequence,challenge,body);
                requestHash=Files.Hash(frame); requestPayloadHash=Files.Hash(body);
                Deadline(); io.Enqueue(frame); Deadline();
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        internal PrivateControlResponse Poll(IndependentWatchdogLaunch owner) {
            EnterOperation();
            try {
                if(consumed || !Object.ReferenceEquals(this.owner,owner)) throw new InvalidOperationException("No outstanding owned control request.");
                Deadline(); RequireOwnerReady(); Deadline();
                byte[] frame=io.Take();
                if(response!=null) {
                    if(frame!=null) throw new InvalidDataException("Duplicate private control response.");
                    io.AssertNoTrailing(); Deadline(); return response;
                }
                if(frame==null) { Deadline(); return null; }
                int actualSequence; byte[] payload; string[] parts=ControlWire.Decode(frame,true,session,out actualSequence,out payload);
                if(actualSequence!=sequence || parts[3]!=challenge || parts[4]!=requestHash) throw new InvalidDataException("Private control response is stale or from another request.");
                RequireOwnerReady(); io.AssertNoTrailing(); Deadline();
                response=new PrivateControlResponse(this,Files.Utf8.GetString(payload),requestHash,requestPayloadHash,challenge,sequence,requestClock.ElapsedMilliseconds);
                Deadline(); return response;
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        internal void Consume(PrivateControlResponse proof) {
            EnterOperation();
            try {
                if(consumed || !Object.ReferenceEquals(response,proof)) throw new InvalidOperationException("Private control observation is not current or already consumed.");
                Deadline(); RequireOwnerReady(); Deadline();
                if(io.Take()!=null) throw new InvalidDataException("Additional private control response precedes consumption.");
                io.AssertNoTrailing(); Deadline(); consumed=true;
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        internal void EnterTerminal(IndependentWatchdogLaunch owner) {
            EnterOperation();
            try {
                if(terminal) throw new InvalidOperationException("Private control terminal mode is single-use.");
                terminal=true;
                if(disposed || !consumed || (this.owner!=null && !Object.ReferenceEquals(this.owner,owner)))
                    throw new InvalidOperationException("Private control terminal mode requires its idle original parent capability.");
                this.owner=owner; io.BeginDrain(false);
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        internal bool PollTerminalDrain(IndependentWatchdogLaunch owner) {
            EnterOperation();
            try {
                if(disposed || !terminal || !Object.ReferenceEquals(this.owner,owner))
                    throw new InvalidOperationException("Private control drain requires its original terminal parent capability.");
                return io.PollDrain();
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        public void Dispose() {
            EnterOperation();
            try { if(disposed) return; if(io!=null) io.Dispose(); disposed=true; }
            finally { ExitOperation(); }
        }
    }

    public sealed class PrivateControlRequest {
        internal readonly PrivateControlEndpoint Owner;
        public readonly string PayloadText,Challenge,Sha256;
        public readonly int Sequence;
        internal PrivateControlRequest(PrivateControlEndpoint owner,string payload,string challenge,string sha,int sequence) {
            Owner=owner; PayloadText=payload; Challenge=challenge; Sha256=sha; Sequence=sequence;
        }
    }

    public sealed class PrivateControlEndpoint : IDisposable {
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint GetFileType(IntPtr handle);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool SetHandleInformation(IntPtr handle,uint mask,uint flags);
        private readonly string session;
        private readonly BoundedControlIo io;
        private int sequence;
        private PrivateControlRequest pending;
        private bool disposed;
        private int operation;
        private bool terminal;
        private Stopwatch drainClock;
        private PrivateControlEndpoint(string inputHandle,string outputHandle,string session) {
            ControlWire.Hex(session,32); this.session=session;
            IntPtr input=ParseHandle(inputHandle),output=ParseHandle(outputHandle);
            if(input==output || GetFileType(input)!=3 || GetFileType(output)!=3) throw new InvalidDataException("Distinct private inherited pipe endpoints required.");
            // Clear inheritable flags before any Docker or other descendant launch.
            if(!SetHandleInformation(input,1,0) || !SetHandleInformation(output,1,0)) throw new Win32Exception(Marshal.GetLastWin32Error(),"Make control endpoints non-inheritable");
            AnonymousPipeClientStream read=null,write=null;
            try {
                read=new AnonymousPipeClientStream(PipeDirection.In,new SafePipeHandle(input,true));
                write=new AnonymousPipeClientStream(PipeDirection.Out,new SafePipeHandle(output,true));
                io=new BoundedControlIo(read,write); io.Start();
            } catch(Exception failure) {
                if(io!=null) {
                    // A partially started reader may still own the pipe. Do not
                    // close it behind that thread merely because Open failed.
                    io.Fail(failure);
                    try { io.Dispose(); } catch(Exception cleanup) { failure=Files.Combine(failure,cleanup); }
                } else {
                    foreach(Stream stream in new Stream[] {read,write}) if(stream!=null)
                        try { stream.Dispose(); } catch(Exception cleanup) { failure=Files.Combine(failure,cleanup); }
                }
                throw failure;
            }
        }
        private static IntPtr ParseHandle(string value) {
            long parsed;
            if(!Int64.TryParse(value,NumberStyles.None,CultureInfo.InvariantCulture,out parsed) || parsed<=0 ||
                parsed.ToString(CultureInfo.InvariantCulture)!=value || (IntPtr.Size==4 && parsed>Int32.MaxValue))
                throw new InvalidDataException("Canonical inherited control handle required.");
            return new IntPtr(parsed);
        }
        public static PrivateControlEndpoint Open(string inputHandle,string outputHandle,string sessionId) {
            return new PrivateControlEndpoint(inputHandle,outputHandle,sessionId);
        }
        private void EnterOperation() {
            if(Interlocked.CompareExchange(ref operation,1,0)!=0) {
                Exception error=new InvalidOperationException("Concurrent private control endpoint operation is forbidden.");
                io.Fail(error); throw error;
            }
        }
        private void ExitOperation() { Interlocked.Exchange(ref operation,0); }
        private void CheckHealth() {
            if(disposed) throw new ObjectDisposedException("PrivateControlEndpoint");
            if(terminal) throw new InvalidOperationException("Private control endpoint cannot return to live service after terminal admission.");
            io.AssertHealthy();
        }
        public void AssertHealthy() {
            EnterOperation();
            try { CheckHealth(); } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        public PrivateControlRequest PollRequest() {
            EnterOperation();
            try {
                CheckHealth(); byte[] frame=io.Take();
                if(pending!=null) {
                    if(frame!=null) throw new InvalidDataException("Concurrent private control request.");
                    return null;
                }
                if(frame==null) return null;
                int actualSequence; byte[] payload; string[] parts=ControlWire.Decode(frame,false,session,out actualSequence,out payload);
                if(actualSequence!=sequence+1) throw new InvalidDataException("Private control request sequence was replayed or skipped.");
                sequence=actualSequence; pending=new PrivateControlRequest(this,Files.Utf8.GetString(payload),parts[3],Files.Hash(frame),sequence);
                return pending;
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        public void Respond(PrivateControlRequest request,byte[] jsonPayload) {
            EnterOperation();
            try {
                CheckHealth();
                if(request==null || !Object.ReferenceEquals(pending,request) || !Object.ReferenceEquals(request.Owner,this))
                    throw new InvalidOperationException("Private control reply requires the unconsumed actual request.");
                io.AssertNoTrailing();
                byte[] frame=ControlWire.Response(session,request.Sequence,request.Challenge,request.Sha256,jsonPayload);
                io.Enqueue(frame); pending=null;
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        private void AssertDrainDeadline() {
            if(drainClock==null || drainClock.ElapsedMilliseconds>=30000)
                throw new TimeoutException("Private endpoint drain exceeded its original 30000ms cleanup deadline.");
        }
        public void BeginTerminalDrain() {
            EnterOperation();
            try {
                if(terminal) throw new InvalidOperationException("Private endpoint terminal drain is single-use.");
                drainClock=Stopwatch.StartNew(); terminal=true;
                if(disposed || pending!=null) throw new InvalidOperationException("Private endpoint cannot abandon an outstanding request.");
                io.BeginDrain(true); AssertDrainDeadline();
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        public bool PollTerminalDrain() {
            EnterOperation();
            try {
                if(disposed || !terminal) throw new InvalidOperationException("Private endpoint terminal drain has not begun.");
                AssertDrainDeadline(); bool complete=io.PollDrain(); AssertDrainDeadline(); return complete;
            } catch(Exception error) { io.Fail(error); throw; }
            finally { ExitOperation(); }
        }
        public void Dispose() {
            EnterOperation();
            try { if(disposed) return; io.Dispose(); disposed=true; }
            finally { ExitOperation(); }
        }
    }

    // The session and command lifetimes are deliberately separate. These APIs
    // are not an import/build/R1 or Docker-containment authorization. The full
    // launcher/receiver ABI must bind the inherited capability before use.
    internal static class CommandJobNative {
        [StructLayout(LayoutKind.Sequential)] internal struct BasicLimit {
            internal long processTime,jobTime; internal uint flags; internal UIntPtr min,max;
            internal uint active; internal UIntPtr affinity; internal uint priority,scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] internal struct IoCounters { internal ulong a,b,c,d,e,f; }
        [StructLayout(LayoutKind.Sequential)] internal struct ExtendedLimit {
            internal BasicLimit basic; internal IoCounters io; internal UIntPtr processMemory,jobMemory,peakProcess,peakJob;
        }
        [StructLayout(LayoutKind.Sequential)] internal struct Accounting {
            internal long user,kernel,periodUser,periodKernel; internal uint faults,total,active,terminated;
        }
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern IntPtr CreateJobObject(IntPtr security,string name);
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool SetInformationJobObject(IntPtr job,int type,ref ExtendedLimit limits,uint size);
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool QueryInformationJobObject(IntPtr job,int type,out Accounting accounting,uint size,IntPtr returned);
        [DllImport("kernel32.dll",EntryPoint="QueryInformationJobObject",SetLastError=true)] internal static extern bool QueryLimits(IntPtr job,int type,out ExtendedLimit limits,uint size,IntPtr returned);
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool TerminateJobObject(IntPtr job,uint exitCode);
        [DllImport("kernel32.dll")] internal static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool DuplicateHandle(IntPtr sourceProcess,IntPtr source,IntPtr targetProcess,out IntPtr target,uint access,bool inherit,uint options);
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool GetHandleInformation(IntPtr handle,out uint flags);
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool SetHandleInformation(IntPtr handle,uint mask,uint flags);
        [DllImport("kernel32.dll",SetLastError=true)] internal static extern bool CloseHandle(IntPtr handle);
        internal static void Check(bool okay,string operation) {
            if(!okay) throw new Win32Exception(Marshal.GetLastWin32Error(),operation);
        }
        internal static void RequireCommandJob(IntPtr handle) {
            if(handle==IntPtr.Zero || handle==new IntPtr(-1)) throw new InvalidDataException("A real command Job handle is required.");
            ExtendedLimit limits;
            Check(QueryLimits(handle,9,out limits,(uint)Marshal.SizeOf(typeof(ExtendedLimit)),IntPtr.Zero),"Command Job limits");
            if(limits.basic.flags!=0x2000) throw new InvalidDataException("Command Job requires exact kill-on-close without breakaway limits.");
        }
    }

    // Parent-side C retained by the managed Transition factory before Initialize.
    // Its actual inherited admission is bound to the original suspended T; the
    // qualified assembly/import and T command-use ABI remain a separate barrier.
    internal sealed class CommandSessionOwner : IDisposable {
        private readonly object stateGate=new object();
        private IntPtr job,launchCopy;
        private readonly IndependentTransitionLaunch creator;
        private bool initializeAttempted,jobCreationReturned,initialized,admissionAttempted,admissionReturned,admissionSucceeded,admissionIssued;
        private bool bindingAttempted,bindingSucceeded,retirementStarted,retired,disposed;
        private Stopwatch retirementClock;
        internal CommandSessionOwner(IndependentTransitionLaunch originalCreator) {
            if(originalCreator==null) throw new ArgumentNullException("originalCreator");
            creator=originalCreator;
        }
        internal void Initialize() {
            lock(stateGate) {
                if(disposed || initializeAttempted || retirementStarted) throw new InvalidOperationException("Command session initialization is single-use.");
                initializeAttempted=true;
                job=CommandJobNative.CreateJobObject(IntPtr.Zero,null);
                jobCreationReturned=true;
                CommandJobNative.Check(job!=IntPtr.Zero,"Create private command session Job");
                CommandJobNative.ExtendedLimit limits=new CommandJobNative.ExtendedLimit(); limits.basic.flags=0x2000;
                CommandJobNative.Check(CommandJobNative.SetInformationJobObject(job,9,ref limits,(uint)Marshal.SizeOf(limits)),"Command session kill-on-close");
                CommandJobNative.RequireCommandJob(job); initialized=true;
            }
        }
        internal IntPtr DuplicateAdmissionForLaunch() {
            lock(stateGate) {
                if(disposed || !initialized || admissionAttempted || retirementStarted)
                    throw new InvalidOperationException("Command session admission may be issued only once.");
                CommandJobNative.RequireCommandJob(job);
                IntPtr copy=IntPtr.Zero;
                // ASSIGN_PROCESS | QUERY only; never SAME_ACCESS, SET_ATTRIBUTES
                // or TERMINATE. JOB_LIST support with these rights must qualify.
                admissionAttempted=true;
                bool copied=CommandJobNative.DuplicateHandle(CommandJobNative.GetCurrentProcess(),job,
                    CommandJobNative.GetCurrentProcess(),out copy,0x0005,true,0);
                launchCopy=copy; admissionSucceeded=copied; admissionReturned=true;
                CommandJobNative.Check(copied,"Duplicate restricted command admission");
                if(launchCopy==IntPtr.Zero || launchCopy==new IntPtr(-1)) throw new InvalidDataException("Restricted admission copy is invalid.");
                admissionIssued=true; return launchCopy;
            }
        }
        internal void BindCreator() {
            lock(stateGate) {
                if(disposed || !initialized || !admissionIssued || launchCopy==IntPtr.Zero || bindingAttempted)
                    throw new InvalidOperationException("Original suspended command creator binding is required exactly once.");
                // Retain the actual capability even when its suspend/identity
                // inspection fails; caller flags or PIDs cannot replace it.
                bindingAttempted=true; creator.AssertSuspended(); bindingSucceeded=true;
            }
        }
        internal void AssertCreatorBoundForResume() {
            lock(stateGate) {
                if(disposed || !initialized || !admissionIssued || !bindingSucceeded || launchCopy!=IntPtr.Zero || retirementStarted)
                    throw new InvalidOperationException("Transition Resume requires checked original command admission binding and closed parent copy.");
                CommandJobNative.RequireCommandJob(job);
            }
        }
        internal void CloseLaunchCopy() {
            lock(stateGate) {
                if(disposed) throw new ObjectDisposedException("CommandSessionOwner");
                AssertAdmissionOutcomeKnown();
                if(launchCopy==IntPtr.Zero) return;
                CommandJobNative.Check(CommandJobNative.CloseHandle(launchCopy),"Close parent command admission copy");
                launchCopy=IntPtr.Zero;
            }
        }
        private void AssertAdmissionOutcomeKnown() {
            // A nonzero failed DuplicateHandle output is not an owned handle.
            // Likewise a native exception or invalid success cannot be closed.
            if(admissionAttempted && (!admissionReturned || (admissionSucceeded && !admissionIssued) ||
                (!admissionSucceeded && launchCopy!=IntPtr.Zero)))
                throw new InvalidOperationException("Ambiguous command admission export must remain retained without handle operations.");
        }
        private void AssertRetirementDeadline() {
            if(retirementClock==null || retirementClock.ElapsedMilliseconds>=30000)
                throw new TimeoutException("Command session retirement exceeded its original cleanup deadline; handles retained.");
        }
        internal bool PollRetirement() {
            lock(stateGate) {
                AssertAdmissionOutcomeKnown();
                if(disposed || (initializeAttempted && !jobCreationReturned))
                    throw new InvalidOperationException("Ambiguous original command-session creation/export must remain retained.");
                if(!retirementStarted) { retirementStarted=true; retirementClock=Stopwatch.StartNew(); }
                AssertRetirementDeadline();
                IndependentTransitionLaunch.CommandCreatorDisposition disposition=creator.GetCommandCreatorDisposition();
                if(disposition==IndependentTransitionLaunch.CommandCreatorDisposition.NeverCreated) {
                    creator.AssertNoChildCreated(); AssertRetirementDeadline(); CloseLaunchCopy();
                    if(job!=IntPtr.Zero) {
                        CommandJobNative.Accounting empty;
                        CommandJobNative.Check(CommandJobNative.QueryInformationJobObject(job,1,out empty,
                            (uint)Marshal.SizeOf(typeof(CommandJobNative.Accounting)),IntPtr.Zero),"Uninherited command session accounting");
                        creator.AssertNoChildCreated(); AssertRetirementDeadline();
                        if(empty.active!=0) return false;
                    }
                    creator.AssertNoChildCreated(); AssertRetirementDeadline(); retired=true; return true;
                }
                if(!initialized || !admissionIssued)
                    throw new InvalidOperationException("A materialized creator requires the original initialized command admission.");
                creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline();
                // A failed initial BindCreator may be recovered only by this
                // fresh original native death proof, never its attempted flag.
                CloseLaunchCopy(); creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline();
                // This can kill every nested D, so it is allowed only AFTER the
                // original creator's physical death, never from a Job snapshot.
                if(!retired) {
                    CommandJobNative.Check(CommandJobNative.TerminateJobObject(job,201),"Retired creator command session termination");
                    AssertRetirementDeadline();
                }
                CommandJobNative.Accounting accounting;
                CommandJobNative.Check(CommandJobNative.QueryInformationJobObject(job,1,out accounting,
                    (uint)Marshal.SizeOf(typeof(CommandJobNative.Accounting)),IntPtr.Zero),"Command session descendant accounting");
                creator.AssertCommandCreatorTerminated(); AssertRetirementDeadline();
                if(accounting.active!=0) return false;
                retired=true; return true;
            }
        }
        public void Dispose() {
            lock(stateGate) {
                if(disposed) return;
                AssertAdmissionOutcomeKnown();
                if(launchCopy!=IntPtr.Zero) throw new InvalidOperationException("Outstanding command admission copy must remain retained.");
                if(!retired || (initializeAttempted && !jobCreationReturned) || (admissionAttempted && !admissionReturned))
                    throw new InvalidOperationException("Unretired or ambiguous command session cannot be disposed.");
                IndependentTransitionLaunch.CommandCreatorDisposition disposition=creator.GetCommandCreatorDisposition();
                if(disposition==IndependentTransitionLaunch.CommandCreatorDisposition.NeverCreated) creator.AssertNoChildCreated();
                else creator.AssertCommandCreatorTerminated();
                if(job!=IntPtr.Zero) {
                    CommandJobNative.Accounting accounting;
                    CommandJobNative.Check(CommandJobNative.QueryInformationJobObject(job,1,out accounting,
                        (uint)Marshal.SizeOf(typeof(CommandJobNative.Accounting)),IntPtr.Zero),"Final command session accounting");
                    if(accounting.active!=0) throw new InvalidOperationException("Command session still has descendants; handles retained.");
                    if(disposition==IndependentTransitionLaunch.CommandCreatorDisposition.NeverCreated) creator.AssertNoChildCreated();
                    else creator.AssertCommandCreatorTerminated();
                    CommandJobNative.Check(CommandJobNative.CloseHandle(job),"Close original command session Job");
                    job=IntPtr.Zero;
                }
                disposed=true;
            }
        }
    }

    // T-side restricted C handle. Adoption does not prove the import/build/R1
    // contract or permit any service operation. Fixed native launch provenance
    // must bind the original inherited handle, never a Job name or PID lookup.
    public sealed class CommandSessionClient : IDisposable {
        private readonly object stateGate=new object();
        private IntPtr job;
        private NativeCommand activeCommand;
        private bool disposed;
        private CommandSessionClient(IntPtr inherited) { job=inherited; }
        public static CommandSessionClient AdoptInherited(string inheritedHandle) {
            ulong number;
            if(String.IsNullOrEmpty(inheritedHandle) || !UInt64.TryParse(inheritedHandle,NumberStyles.None,CultureInfo.InvariantCulture,out number) ||
                number==0 || number.ToString(CultureInfo.InvariantCulture)!=inheritedHandle ||
                (IntPtr.Size==4 ? number>=UInt32.MaxValue : number>=Int64.MaxValue))
                throw new ArgumentException("Canonical real inherited command Job handle required.");
            IntPtr handle=IntPtr.Size==4 ? new IntPtr(unchecked((int)(uint)number)) : new IntPtr((long)number);
            uint flags;
            CommandJobNative.Check(CommandJobNative.GetHandleInformation(handle,out flags),"Inherited command admission flags");
            if((flags & 1)==0) throw new InvalidDataException("Command admission is not an inherited handle.");
            // Clear inheritance before any later external command. Do not widen
            // rights or manufacture a new C when verification fails.
            CommandJobNative.Check(CommandJobNative.SetHandleInformation(handle,1,0),"Clear command admission inheritance");
            CommandJobNative.Check(CommandJobNative.GetHandleInformation(handle,out flags),"Recheck command admission flags");
            if((flags & 1)!=0) throw new InvalidDataException("Command admission remained inheritable.");
            CommandJobNative.RequireCommandJob(handle);
            return new CommandSessionClient(handle);
        }
        public NativeCommand ActiveCommand { get { lock(stateGate) { return activeCommand; } } }
        internal IntPtr RegisterCommand(NativeCommand command) {
            // Managed-only registration. The object remains rooted before its
            // first native operation, including partially failed initialization.
            lock(stateGate) {
                if(disposed || job==IntPtr.Zero || command==null || activeCommand!=null)
                    throw new InvalidOperationException("Command admission requires a live idle session.");
                activeCommand=command; return job;
            }
        }
        internal void ReleaseCommand(NativeCommand command) {
            lock(stateGate) {
                if(disposed || command==null || !Object.ReferenceEquals(activeCommand,command))
                    throw new InvalidOperationException("Only the registered native command may release its slot.");
                activeCommand=null;
            }
        }
        public void Dispose() {
            lock(stateGate) {
                if(disposed) return;
                if(activeCommand!=null) throw new InvalidOperationException("Active or unresolved native command must remain retained.");
                CommandJobNative.Check(CommandJobNative.CloseHandle(job),"Close adopted command session handle");
                job=IntPtr.Zero; disposed=true;
            }
        }
    }

    public sealed class NativeResult {
        public int ExitCode;
        public string Stdout, Stderr;
        public int ProcessId;
        public string StartedAt;
        public long StdoutBytes, StderrBytes;
    }

    // Original first-line bytes, not a parsed readiness claim or authorization.
    // Text and ByteCount include the exact final LF captured from the pipe.
    public sealed class NativeOutputFrame {
        public readonly string Text, Sha256, StartedAt;
        public readonly long ByteCount;
        public readonly int ProcessId;
        internal NativeOutputFrame(string text, string sha256, long byteCount, int processId, string startedAt) {
            Text = text; Sha256 = sha256; ByteCount = byteCount;
            ProcessId = processId; StartedAt = startedAt;
        }
    }

    // Historical, immutable successful per-command observation. It is not a
    // session-C empty proof, daemon-state proof or guard-retirement permission.
    public sealed class NativeCommandResult {
        public readonly int ExitCode, ProcessId;
        public readonly string StartedAt, Stdout, Stderr;
        public readonly long StdoutBytes, StderrBytes, ElapsedMilliseconds;
        public readonly bool SessionTreeEmptyProven=false, ContainmentAccepted=false, RetirementPermitted=false;
        internal NativeCommandResult(int pid,string started,string stdout,string stderr,long stdoutBytes,long stderrBytes,long elapsed) {
            ExitCode=0; ProcessId=pid; StartedAt=started; Stdout=stdout; Stderr=stderr;
            StdoutBytes=stdoutBytes; StderrBytes=stderrBytes; ElapsedMilliseconds=elapsed;
        }
    }

    // A retained session C owns every command, while a fresh nested D is the
    // only Job this object may terminate or close. Prepare registers the object
    // before any native work, including failures during suspended construction.
    public sealed class NativeCommand : IDisposable {
        [StructLayout(LayoutKind.Sequential)] private struct Startup {
            public int cb; public IntPtr reserved,desktop,title;
            public int x,y,xSize,ySize,xChars,yChars,fill,flags;
            public short show,reserved2; public IntPtr reservedPtr,stdin,stdout,stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr process,thread; public int pid,tid; }
        [StructLayout(LayoutKind.Sequential)] private struct BasicLimit {
            public long processTime,jobTime; public uint flags; public UIntPtr min,max;
            public uint active; public UIntPtr affinity; public uint priority,scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong a,b,c,d,e,f; }
        [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit {
            public BasicLimit basic; public IoCounters io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob;
        }
        [StructLayout(LayoutKind.Sequential)] private struct Accounting {
            public long user,kernel,periodUser,periodKernel; public uint faults,total,active,terminated;
        }
        [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)]
        private static extern bool CreateProcess(string app,StringBuilder args,IntPtr processAttributes,IntPtr threadAttributes,
            bool inherit,uint flags,IntPtr environment,string directory,ref StartupEx startup,out ProcessInfo info);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern IntPtr CreateJobObject(IntPtr security,string name);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool SetInformationJobObject(IntPtr job,int type,ref ExtendedLimit limits,uint size);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool QueryInformationJobObject(IntPtr job,int type,out Accounting accounting,uint size,IntPtr returned);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool IsProcessInJob(IntPtr process,IntPtr job,out bool member);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool TerminateJobObject(IntPtr job,uint code);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint WaitForSingleObject(IntPtr handle,uint milliseconds);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint SuspendThread(IntPtr thread);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetExitCodeProcess(IntPtr process,out uint code);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool GetProcessTimes(IntPtr process,out long created,out long exited,out long kernel,out long user);
        [DllImport("kernel32.dll",SetLastError=true,CharSet=CharSet.Unicode)] private static extern bool QueryFullProcessImageName(IntPtr process,uint flags,StringBuilder name,ref uint size);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern uint GetProcessId(IntPtr process);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list,int count,int flags,ref IntPtr size);
        [DllImport("kernel32.dll",SetLastError=true)] private static extern bool UpdateProcThreadAttribute(IntPtr list,uint flags,IntPtr attribute,IntPtr value,IntPtr size,IntPtr previous,IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        private readonly CommandSessionClient session;
        private readonly string executable,expectedHash,directory,commandLine;
        private readonly FileStream heldExecutable;
        private readonly int timeoutMilliseconds,maximumBytes;
        private readonly Stopwatch successClock;
        private readonly object sync=new object();
        private readonly List<Exception> failures=new List<Exception>();
        private readonly MemoryStream stdoutBytes=new MemoryStream(),stderrBytes=new MemoryStream();
        private IntPtr sessionJob,commandJob,process,thread;
        private AnonymousPipeServerStream output,error,input;
        private Thread outputThread,errorThread;
        private bool outputClientClosed,errorClientClosed,inputClientClosed,stdoutEof,stderrEof;
        private bool startAttempted,creationSucceeded,resumeAttempted,resumed,containmentStarted,closureProven,releaseStarted,disposed;
        private bool outputDisposed,errorDisposed,inputDisposed;
        private int pid,operation,collectorTerminationAttempted;
        private long birth;
        private string startedAt;
        private Stopwatch containmentClock;
        private NativeCommandResult completedResult;
        private NativeCommand(CommandSessionClient session,string executable,string hash,FileStream heldExecutable,
                string commandLine,string directory,int timeout,int maximum,Stopwatch clock) {
            this.session=session; this.executable=executable; expectedHash=hash; this.heldExecutable=heldExecutable;
            this.commandLine=commandLine; this.directory=directory; timeoutMilliseconds=timeout; maximumBytes=maximum; successClock=clock;
        }
        private static void CanonicalCommandPath(string path) {
            if(path==null || !System.Text.RegularExpressions.Regex.IsMatch(path,"\\A[A-Za-z]:\\\\") ||
                path.IndexOf(':',2)>=0 || Path.GetFullPath(path)!=path)
                throw new ArgumentException("Canonical drive-local command path required.");
            if(path.Length==3) return;
            foreach(string part in path.Substring(3).Split('\\')) {
                if(part.Length==0 || part=="." || part==".." || part.EndsWith(".") || part.EndsWith(" "))
                    throw new ArgumentException("Ambiguous command path component.");
                foreach(char c in part) if(c<32 || c=='"' || c=='<' || c=='>' || c=='|' || c=='*' || c=='?')
                    throw new ArgumentException("Invalid command path character.");
            }
        }
        public static NativeCommand Prepare(CommandSessionClient session,string executable,string expectedHash,FileStream heldExecutable,
                string[] arguments,string directory,int timeoutMilliseconds,int maximumBytes) {
            Stopwatch clock=Stopwatch.StartNew();
            if(session==null || heldExecutable==null || arguments==null) throw new ArgumentNullException("command inputs");
            if(timeoutMilliseconds!=15000 && timeoutMilliseconds!=25000) throw new ArgumentException("Only fixed proof/STOP command budgets are allowed.");
            if(maximumBytes<1 || maximumBytes>1048576) throw new ArgumentException("Command capture exceeds the fixed one-MiB bound.");
            CanonicalCommandPath(executable); CanonicalCommandPath(directory);
            if(expectedHash==null || !System.Text.RegularExpressions.Regex.IsMatch(expectedHash,"\\A[0-9a-f]{64}\\z"))
                throw new ArgumentException("Exact command executable hash required.");
            if(arguments.Length>256) throw new ArgumentException("Command argument count exceeds its fixed bound.");
            StringBuilder line=new StringBuilder(NativeChild.Quote(executable));
            if(line.Length>32767) throw new ArgumentException("Command executable exceeds the character bound.");
            foreach(string argument in arguments) {
                if(argument==null || argument.Length>32767) throw new ArgumentException("Invalid command argument.");
                line.Append(" ").Append(NativeChild.Quote(argument));
                if(line.Length>32767) throw new ArgumentException("Command line exceeds the Windows byte-independent character bound.");
            }
            NativeCommand command=new NativeCommand(session,executable,expectedHash,heldExecutable,line.ToString(),directory,
                timeoutMilliseconds,maximumBytes,clock);
            command.sessionJob=session.RegisterCommand(command);
            return command;
        }
        private static void Check(bool okay,string label) { if(!okay) throw new Win32Exception(Marshal.GetLastWin32Error(),label); }
        private void Record(Exception error) {
            string text=error.GetType().Name+": "+error.Message;
            if(text.Length>512) text=text.Substring(0,512);
            lock(sync) { if(failures.Count<8) failures.Add(new InvalidOperationException(text)); }
        }
        private void Enter() {
            if(Interlocked.CompareExchange(ref operation,1,0)!=0) {
                Exception error=new InvalidOperationException("Concurrent command lifecycle operation is forbidden."); Record(error); throw error;
            }
        }
        private void Leave() { Interlocked.Exchange(ref operation,0); }
        private void SuccessState() {
            if(disposed || releaseStarted || containmentStarted) throw new InvalidOperationException("Command success is unavailable after cleanup/release begins.");
            lock(sync) { if(failures.Count!=0) throw new AggregateException(failures.ToArray()); }
        }
        private void SuccessBoundary() {
            SuccessState();
            if(successClock.ElapsedMilliseconds>=timeoutMilliseconds) throw new TimeoutException("Original whole-command success budget expired.");
        }
        private void HeldExecutableBoundary() {
            if(heldExecutable.SafeFileHandle.IsClosed || heldExecutable.SafeFileHandle.IsInvalid || !heldExecutable.CanRead ||
                !heldExecutable.CanSeek || !String.Equals(heldExecutable.Name,executable,StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Original executable read lock is unavailable.");
            Files.RegularAncestry(executable,true); Files.RequireSingleLink(heldExecutable);
            if(Files.HashStream(heldExecutable)!=expectedHash) throw new InvalidDataException("Held command executable bytes changed.");
        }
        private uint RootWait() {
            uint wait=WaitForSingleObject(process,0);
            if(wait==UInt32.MaxValue) Check(false,"Exact command root wait");
            if(wait!=0 && wait!=258) throw new InvalidOperationException("Unknown exact command wait status.");
            return wait;
        }
        private Accounting AccountingFor(IntPtr job,string label) {
            Accounting value;
            Check(QueryInformationJobObject(job,1,out value,(uint)Marshal.SizeOf(typeof(Accounting)),IntPtr.Zero),label);
            return value;
        }
        private void RootIdentity(bool establish) {
            if(process==IntPtr.Zero || pid<=0 || GetProcessId(process)!=(uint)pid) throw new InvalidDataException("Original command root identity differs.");
            long created,exited,kernel,user;
            Check(GetProcessTimes(process,out created,out exited,out kernel,out user),"Command root native times");
            if(establish) { birth=created; startedAt=new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o"); }
            else if(created!=birth) throw new InvalidDataException("Command root birth changed.");
            bool member;
            Check(IsProcessInJob(process,sessionJob,out member),"Command root session-C membership");
            if(!member) throw new InvalidDataException("Command root is outside retained session C.");
            Check(IsProcessInJob(process,commandJob,out member),"Command root command-D membership");
            if(!member) throw new InvalidDataException("Command root is outside its own D.");
            StringBuilder image=new StringBuilder(32768); uint count=(uint)image.Capacity;
            Check(QueryFullProcessImageName(process,0,image,ref count),"Command root image");
            if(!String.Equals(Path.GetFullPath(image.ToString()),executable,StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException("Command root image differs.");
        }
        public void StartSuspended() {
            Enter();
            try {
                if(startAttempted) throw new InvalidOperationException("Command native start is single-use.");
                startAttempted=true; SuccessBoundary(); HeldExecutableBoundary(); SuccessBoundary();
                Files.RegularAncestry(directory,true);
                if(sessionJob==IntPtr.Zero) throw new InvalidOperationException("Registered session C is unavailable.");
                AccountingFor(sessionJob,"Retained C access"); SuccessBoundary();
                commandJob=CreateJobObject(IntPtr.Zero,null); Check(commandJob!=IntPtr.Zero,"Create private command D");
                ExtendedLimit limits=new ExtendedLimit(); limits.basic.flags=0x2000;
                Check(SetInformationJobObject(commandJob,9,ref limits,(uint)Marshal.SizeOf(limits)),"Command D kill-on-close");
                output=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                error=new AnonymousPipeServerStream(PipeDirection.In,HandleInheritability.Inheritable);
                input=new AnonymousPipeServerStream(PipeDirection.Out,HandleInheritability.Inheritable);
                IntPtr attributes=IntPtr.Zero,handles=IntPtr.Zero,jobs=IntPtr.Zero; bool initialized=false;
                try {
                    IntPtr size=IntPtr.Zero; InitializeProcThreadAttributeList(IntPtr.Zero,2,0,ref size);
                    if(size==IntPtr.Zero) Check(false,"Command attribute size");
                    attributes=Marshal.AllocHGlobal(size);
                    Check(InitializeProcThreadAttributeList(attributes,2,0,ref size),"Command attributes"); initialized=true;
                    handles=Marshal.AllocHGlobal(IntPtr.Size*3);
                    Marshal.WriteIntPtr(handles,0,input.ClientSafePipeHandle.DangerousGetHandle());
                    Marshal.WriteIntPtr(handles,IntPtr.Size,output.ClientSafePipeHandle.DangerousGetHandle());
                    Marshal.WriteIntPtr(handles,IntPtr.Size*2,error.ClientSafePipeHandle.DangerousGetHandle());
                    Check(UpdateProcThreadAttribute(attributes,0,new IntPtr(0x20002),handles,new IntPtr(IntPtr.Size*3),IntPtr.Zero,IntPtr.Zero),"Command stdio-only inheritance");
                    jobs=Marshal.AllocHGlobal(IntPtr.Size*2); Marshal.WriteIntPtr(jobs,0,sessionJob); Marshal.WriteIntPtr(jobs,IntPtr.Size,commandJob);
                    Check(UpdateProcThreadAttribute(attributes,0,new IntPtr(0x2000D),jobs,new IntPtr(IntPtr.Size*2),IntPtr.Zero,IntPtr.Zero),"Required command birth C/D list");
                    StartupEx startup=new StartupEx(); startup.startup.cb=Marshal.SizeOf(startup); startup.startup.flags=0x101;
                    startup.startup.stdin=input.ClientSafePipeHandle.DangerousGetHandle(); startup.startup.stdout=output.ClientSafePipeHandle.DangerousGetHandle();
                    startup.startup.stderr=error.ClientSafePipeHandle.DangerousGetHandle(); startup.attributes=attributes;
                    SuccessBoundary(); HeldExecutableBoundary(); SuccessBoundary();
                    ProcessInfo info;
                    Check(CreateProcess(executable,new StringBuilder(commandLine),IntPtr.Zero,IntPtr.Zero,true,0x08080004,
                        IntPtr.Zero,directory,ref startup,out info),"Create command suspended in C/D");
                    process=info.process; thread=info.thread; pid=info.pid; creationSucceeded=true;
                } finally {
                    if(initialized) DeleteProcThreadAttributeList(attributes);
                    if(attributes!=IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                    if(handles!=IntPtr.Zero) Marshal.FreeHGlobal(handles);
                    if(jobs!=IntPtr.Zero) Marshal.FreeHGlobal(jobs);
                }
                SuccessBoundary(); RootIdentity(true);
                if(RootWait()!=258 || AccountingFor(commandJob,"Suspended command D accounting").active!=1)
                    throw new InvalidOperationException("New command D does not own exactly its suspended live root.");
                CloseLocalChildEnds(); EnsureCollectors(); HeldExecutableBoundary(); SuccessBoundary();
            } catch(Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        private void CloseLocalChildEnds() {
            if(output!=null && !outputClientClosed) { output.DisposeLocalCopyOfClientHandle(); outputClientClosed=true; }
            if(error!=null && !errorClientClosed) { error.DisposeLocalCopyOfClientHandle(); errorClientClosed=true; }
            if(input!=null && !inputClientClosed) { input.DisposeLocalCopyOfClientHandle(); inputClientClosed=true; }
            if(input!=null && !inputDisposed) { input.Dispose(); inputDisposed=true; }
        }
        private void EnsureCollectors() {
            if(output!=null) {
                if(outputThread==null) { outputThread=new Thread(delegate() { Collect(output,stdoutBytes,false); }); outputThread.IsBackground=true; }
                if((outputThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0) outputThread.Start();
            }
            if(error!=null) {
                if(errorThread==null) { errorThread=new Thread(delegate() { Collect(error,stderrBytes,true); }); errorThread.IsBackground=true; }
                if((errorThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0) errorThread.Start();
            }
        }
        private void Collect(Stream source,MemoryStream destination,bool stderr) {
            try {
                byte[] buffer=new byte[4096];
                while(true) {
                    int count=source.Read(buffer,0,buffer.Length);
                    if(count==0) { lock(sync) { if(stderr) stderrEof=true; else stdoutEof=true; } return; }
                    bool bad;
                    lock(sync) {
                        bad=stderr || destination.Length+count>maximumBytes;
                        if(destination.Length+count<=maximumBytes) destination.Write(buffer,0,count);
                    }
                    if(bad) {
                        Record(new InvalidDataException(stderr ? "Command emitted stderr." : "Command output exceeded its fixed bound."));
                        if(Interlocked.CompareExchange(ref collectorTerminationAttempted,1,0)==0 && commandJob!=IntPtr.Zero && !TerminateJobObject(commandJob,197))
                            Record(new Win32Exception(Marshal.GetLastWin32Error(),"Command D capture-fault containment"));
                        // Drain/discard under the same fixed storage bound until
                        // actual EOF; never turn truncated output into success.
                    }
                }
            } catch(Exception error) { Record(error); }
        }
        public void Resume() {
            Enter();
            try {
                if(!creationSucceeded || resumeAttempted) throw new InvalidOperationException("Command resume requires its original suspended root exactly once.");
                SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false);
                if(RootWait()!=258 || AccountingFor(commandJob,"Command pre-resume D accounting").active!=1)
                    throw new InvalidOperationException("Command suspended ownership differs.");
                uint previous=SuspendThread(thread); if(previous==UInt32.MaxValue) Check(false,"Inspect command suspend count");
                if(previous!=1) throw new InvalidOperationException("Command suspension was disproved; retain the inspection increment for containment.");
                uint restored=ResumeThread(thread); if(restored==UInt32.MaxValue) Check(false,"Restore command inspection increment");
                if(restored!=2) throw new InvalidOperationException("Command suspension restoration was ambiguous.");
                SuccessBoundary(); resumeAttempted=true;
                uint resumedCount=ResumeThread(thread); if(resumedCount==UInt32.MaxValue) Check(false,"Resume exact command");
                if(resumedCount!=1) throw new InvalidOperationException("Command resume was ambiguous.");
                resumed=true; SuccessBoundary();
            } catch(Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        private bool CollectorsClosed() {
            lock(sync) { if((output!=null && !stdoutEof) || (error!=null && !stderrEof)) return false; }
            if(outputThread!=null && ((outputThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0 || !outputThread.Join(0))) return false;
            if(errorThread!=null && ((errorThread.ThreadState & System.Threading.ThreadState.Unstarted)!=0 || !errorThread.Join(0))) return false;
            return true;
        }
        private void RequireSuccessfulRootExit() {
            if(RootWait()!=0 || GetProcessId(process)!=(uint)pid)
                throw new InvalidDataException("Command success requires the original signaled root.");
            long created,exited,kernel,user;
            Check(GetProcessTimes(process,out created,out exited,out kernel,out user),"Successful command native exit times");
            if(birth<=0 || created!=birth || exited<birth || exited==0)
                throw new InvalidDataException("Successful command native birth/exit differs.");
            uint code; Check(GetExitCodeProcess(process,out code),"Command final exit code");
            if(code!=0) throw new InvalidDataException("Command root exited nonzero: "+code.ToString(CultureInfo.InvariantCulture));
        }
        public NativeCommandResult PollResult() {
            Enter();
            try {
                SuccessState();
                // An already frozen result is historical, not a fresh deadline
                // or liveness observation; faults and release still forbid it.
                if(completedResult!=null) return completedResult;
                if(!resumed) throw new InvalidOperationException("Command has not successfully resumed.");
                SuccessBoundary(); HeldExecutableBoundary(); RootIdentity(false); SuccessBoundary();
                uint wait=RootWait(); SuccessBoundary(); if(wait==258) return null;
                RequireSuccessfulRootExit(); SuccessBoundary();
                bool treeClosed=AccountingFor(commandJob,"Command success D accounting").active==0 && CollectorsClosed();
                SuccessBoundary(); if(!treeClosed) return null;
                HeldExecutableBoundary(); RootIdentity(false); SuccessBoundary();
                if(RootWait()!=0 || AccountingFor(commandJob,"Final command D accounting").active!=0 || !CollectorsClosed())
                    throw new InvalidOperationException("Command closure changed at success boundary.");
                RequireSuccessfulRootExit(); SuccessBoundary();
                lock(sync) {
                    SuccessBoundary();
                    if(stderrBytes.Length!=0) throw new InvalidDataException("Command success captured stderr.");
                    string stdout=Files.Utf8.GetString(stdoutBytes.ToArray()),stderr=Files.Utf8.GetString(stderrBytes.ToArray());
                    SuccessBoundary();
                    NativeCommandResult candidate=new NativeCommandResult(pid,startedAt,stdout,stderr,stdoutBytes.Length,stderrBytes.Length,successClock.ElapsedMilliseconds);
                    SuccessBoundary(); completedResult=candidate;
                    closureProven=true; return completedResult;
                }
            } catch(Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        private void ContainmentBoundary() {
            if(containmentClock==null || containmentClock.ElapsedMilliseconds>=30000)
                throw new TimeoutException("Original command cleanup budget expired; C/D/native capabilities remain retained.");
        }
        private bool ContainmentRootClosed() {
            if(!creationSucceeded) return true;
            if(RootWait()!=0) return false;
            if(process==IntPtr.Zero || pid<=0 || GetProcessId(process)!=(uint)pid)
                throw new InvalidDataException("Containment lost original command PID/handle identity.");
            long created,exited,kernel,user;
            Check(GetProcessTimes(process,out created,out exited,out kernel,out user),"Contained command native times");
            if(created<=0 || exited<created || exited==0 || (birth!=0 && created!=birth))
                throw new InvalidDataException("Contained command native birth/exit differs.");
            return RootWait()==0;
        }
        public bool PollContainment() {
            Enter();
            try {
                if(disposed) throw new ObjectDisposedException("NativeCommand");
                if(releaseStarted) throw new InvalidOperationException("Command resource release already began; retry Dispose only.");
                if(closureProven) return true;
                if(!containmentStarted) {
                    containmentClock=Stopwatch.StartNew(); containmentStarted=true;
                    Record(new OperationCanceledException("Command entered irreversible failure containment."));
                }
                ContainmentBoundary();
                if(commandJob!=IntPtr.Zero) Check(TerminateJobObject(commandJob,200),"Terminate command D only");
                CloseLocalChildEnds(); EnsureCollectors(); ContainmentBoundary();
                bool closed=ContainmentRootClosed() &&
                    (commandJob==IntPtr.Zero || AccountingFor(commandJob,"Containment D empty proof").active==0) && CollectorsClosed();
                ContainmentBoundary(); if(!closed) return false;
                closed=ContainmentRootClosed() && (commandJob==IntPtr.Zero || AccountingFor(commandJob,"Final containment D empty proof").active==0);
                ContainmentBoundary(); if(!closed) return false;
                ContainmentBoundary(); closureProven=true; return true;
            } catch(Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
        private static void CloseOwned(ref IntPtr handle) {
            if(handle!=IntPtr.Zero) { Check(CloseHandle(handle),"Release proved-closed command handle"); handle=IntPtr.Zero; }
        }
        public void Dispose() {
            Enter();
            try {
                if(disposed) return;
                if(!closureProven || !CollectorsClosed()) throw new InvalidOperationException("Unproved command closure retains native capabilities and session registration.");
                releaseStarted=true;
                if(input!=null && !inputDisposed) { input.Dispose(); inputDisposed=true; }
                if(output!=null && !outputDisposed) { output.Dispose(); outputDisposed=true; }
                if(error!=null && !errorDisposed) { error.Dispose(); errorDisposed=true; }
                CloseOwned(ref thread); CloseOwned(ref process); CloseOwned(ref commandJob);
                stdoutBytes.Dispose(); stderrBytes.Dispose();
                session.ReleaseCommand(this);
                disposed=true;
                // sessionJob is borrowed: never close/terminate it, including on
                // command timeout, overflow, cleanup failure or owner P death.
            } catch(Exception error) { Record(error); throw; }
            finally { Leave(); }
        }
    }

    // Birth-time Job assignment is part of CreateProcess, before it returns a
    // suspended child. Unsupported JOB_LIST hosts fail closed; there is no
    // post-create assignment fallback. This is per-child ownership, not the
    // independently retained session command Job required by G/T takeover.
    public sealed class NativeChild : IDisposable {
        [StructLayout(LayoutKind.Sequential)] private struct Startup {
            public int cb; public IntPtr reserved, desktop, title;
            public int x,y,xSize,ySize,xChars,yChars,fill,flags;
            public short show,reserved2; public IntPtr reservedPtr,stdin,stdout,stderr;
        }
        [StructLayout(LayoutKind.Sequential)] private struct StartupEx { public Startup startup; public IntPtr attributes; }
        [StructLayout(LayoutKind.Sequential)] private struct ProcessInfo { public IntPtr process,thread; public int pid,tid; }
        [StructLayout(LayoutKind.Sequential)] private struct BasicLimit {
            public long processTime,jobTime; public uint flags; public UIntPtr min,max;
            public uint active; public UIntPtr affinity; public uint priority,scheduling;
        }
        [StructLayout(LayoutKind.Sequential)] private struct IoCounters { public ulong a,b,c,d,e,f; }
        [StructLayout(LayoutKind.Sequential)] private struct ExtendedLimit {
            public BasicLimit basic; public IoCounters io; public UIntPtr processMemory,jobMemory,peakProcess,peakJob;
        }
        [StructLayout(LayoutKind.Sequential)] private struct Accounting {
            public long user,kernel,periodUser,periodKernel; public uint faults,total,active,terminated;
        }
        [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)]
        private static extern bool CreateProcess(string app, StringBuilder args, IntPtr processAttributes,
            IntPtr threadAttributes, bool inherit, uint flags, IntPtr environment, string directory,
            ref StartupEx startup, out ProcessInfo process);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern IntPtr CreateJobObject(IntPtr security, string name);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool SetInformationJobObject(IntPtr job, int type, ref ExtendedLimit limits, uint size);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool QueryInformationJobObject(IntPtr job, int type, out Accounting accounting, uint size, IntPtr returned);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool TerminateJobObject(IntPtr job, uint code);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint SuspendThread(IntPtr thread);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern uint WaitForSingleObject(IntPtr handle, uint time);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetExitCodeProcess(IntPtr process, out uint code);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
        [DllImport("kernel32.dll", SetLastError=true, CharSet=CharSet.Unicode)] private static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attribute, IntPtr value, IntPtr size, IntPtr previous, IntPtr returned);
        [DllImport("kernel32.dll")] private static extern void DeleteProcThreadAttributeList(IntPtr list);
        private IntPtr job,process,thread;
        private AnonymousPipeServerStream output,error,input;
        private Thread outputThread,errorThread;
        private MemoryStream outputBytes = new MemoryStream(), errorBytes = new MemoryStream();
        private readonly object failuresGate = new object();
        private readonly object captureGate = new object();
        private readonly List<Exception> failures = new List<Exception>();
        private int limit;
        private bool resumed,resumeAttempted,disposed;
        private readonly Stopwatch runClock = new Stopwatch();
        private NativeResult completedResult;
        private int observationLifetimeMilliseconds, firstFrameMaximumBytes;
        private NativeOutputFrame firstOutputFrame;
        public readonly int ProcessId;
        public readonly string StartedAt;
        private NativeChild(string executable, string[] arguments, string directory, int maximumBytes) {
            limit = maximumBytes;
            if (limit < 1 || limit > 1048576) throw new ArgumentException("Invalid output byte bound.");
            IntPtr attributes = IntPtr.Zero, handles = IntPtr.Zero, jobs = IntPtr.Zero;
            bool initialized = false;
            try {
                job = CreateJobObject(IntPtr.Zero, null); Check(job != IntPtr.Zero, "CreateJobObject");
                ExtendedLimit limits = new ExtendedLimit(); limits.basic.flags = 0x2000;
                Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)), "Job kill-on-close");
                output = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
                error = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
                input = new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.Inheritable);
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 2, 0, ref size);
                if (size == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Attribute size");
                attributes = Marshal.AllocHGlobal(size);
                Check(InitializeProcThreadAttributeList(attributes, 2, 0, ref size), "Attribute list"); initialized = true;
                handles = Marshal.AllocHGlobal(IntPtr.Size * 3);
                Marshal.WriteIntPtr(handles, 0, input.ClientSafePipeHandle.DangerousGetHandle());
                Marshal.WriteIntPtr(handles, IntPtr.Size, output.ClientSafePipeHandle.DangerousGetHandle());
                Marshal.WriteIntPtr(handles, IntPtr.Size * 2, error.ClientSafePipeHandle.DangerousGetHandle());
                Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), handles,
                    new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero), "Explicit inherited handle list");
                // PROC_THREAD_ATTRIBUTE_JOB_LIST = INPUT | JobList(13). The
                // Job handle is an attribute value, NOT an inherited handle.
                // Keep both value buffers alive until the attribute list dies.
                jobs = Marshal.AllocHGlobal(IntPtr.Size);
                Marshal.WriteIntPtr(jobs, 0, job);
                Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x2000D), jobs,
                    new IntPtr(IntPtr.Size), IntPtr.Zero, IntPtr.Zero), "Required birth-time Job list");
                StartupEx start = new StartupEx();
                start.startup.cb = Marshal.SizeOf(start); start.startup.flags = 0x101;
                start.startup.stdin = input.ClientSafePipeHandle.DangerousGetHandle();
                start.startup.stdout = output.ClientSafePipeHandle.DangerousGetHandle();
                start.startup.stderr = error.ClientSafePipeHandle.DangerousGetHandle();
                start.attributes = attributes;
                StringBuilder command = new StringBuilder(Quote(executable));
                foreach (string arg in arguments) command.Append(" ").Append(Quote(arg));
                ProcessInfo info;
                Check(CreateProcess(executable, command, IntPtr.Zero, IntPtr.Zero, true,
                    0x08080004, IntPtr.Zero, directory, ref start, out info), "CreateProcess suspended");
                process = info.process; thread = info.thread; ProcessId = info.pid;
                bool member;
                Check(IsProcessInJob(process, job, out member), "Verify suspended Job membership");
                if (!member) throw new InvalidOperationException("Suspended child Job membership is false.");
                long created, exited, kernelTime, userTime;
                Check(GetProcessTimes(process, out created, out exited, out kernelTime, out userTime), "Suspended child creation time");
                StartedAt = new DateTimeOffset(DateTime.FromFileTimeUtc(created)).ToString("o");
                StringBuilder processImage = new StringBuilder(32768); uint imageCharacters = (uint)processImage.Capacity;
                Check(QueryFullProcessImageName(process, 0, processImage, ref imageCharacters), "Suspended child image identity");
                if (!String.Equals(Path.GetFullPath(processImage.ToString()), executable,
                        StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("Child image identity differs.");
                output.DisposeLocalCopyOfClientHandle(); error.DisposeLocalCopyOfClientHandle();
                input.DisposeLocalCopyOfClientHandle(); input.Dispose(); input = null;
                outputThread = new Thread(delegate() { Collect(output, outputBytes); }); outputThread.IsBackground = true;
                errorThread = new Thread(delegate() { Collect(error, errorBytes); }); errorThread.IsBackground = true;
                outputThread.Start(); errorThread.Start();
            } catch (Exception primary) {
                try { Dispose(); } catch (Exception cleanup) { throw new AggregateException(primary, cleanup); }
                throw;
            } finally {
                if (initialized) DeleteProcThreadAttributeList(attributes);
                if (attributes != IntPtr.Zero) Marshal.FreeHGlobal(attributes);
                if (handles != IntPtr.Zero) Marshal.FreeHGlobal(handles);
                if (jobs != IntPtr.Zero) Marshal.FreeHGlobal(jobs);
            }
        }
        public static NativeChild StartSuspended(string executable, string expectedHash,
                FileStream heldExecutable, string[] arguments, string directory, int maximumBytes) {
            Files.RegularAncestry(executable, true);
            if (!String.Equals(heldExecutable.Name, executable, StringComparison.OrdinalIgnoreCase) ||
                !heldExecutable.CanRead || Files.HashStream(heldExecutable) != expectedHash)
                throw new InvalidDataException("Exact executable read lock required through child completion.");
            return new NativeChild(executable, arguments, directory, maximumBytes);
        }
        private static void Check(bool okay, string operation) {
            if (!okay) throw new Win32Exception(Marshal.GetLastWin32Error(), operation);
        }
        private void Record(Exception error) { lock(failuresGate) { failures.Add(error); } }
        private bool Failed { get { lock(failuresGate) { return failures.Count != 0; } } }
        private Exception[] FailureSnapshot() { lock(failuresGate) { return failures.ToArray(); } }
        private static uint ClassifiedWait(IntPtr handle, uint milliseconds, string label, List<Exception> errors) {
            uint result = WaitForSingleObject(handle, milliseconds);
            if (result == 0 || result == 258) return result;
            errors.Add(result == UInt32.MaxValue ? (Exception)new Win32Exception(Marshal.GetLastWin32Error(), label) :
                new InvalidOperationException(label + " returned an unknown wait status: " + result));
            return result;
        }
        private void StopOwnedProcessAndJob(List<Exception> errors, string label) {
            if (process != IntPtr.Zero) {
                uint before = ClassifiedWait(process, 0, label + " initial wait", errors);
                if (before != 0 && !TerminateProcess(process, 200))
                    errors.Add(new Win32Exception(Marshal.GetLastWin32Error(), label + " exact process termination"));
            }
            if (job != IntPtr.Zero && !TerminateJobObject(job, 200))
                errors.Add(new Win32Exception(Marshal.GetLastWin32Error(), label + " Job termination"));
            if (process != IntPtr.Zero) {
                uint after = ClassifiedWait(process, 5000, label + " final wait", errors);
                if (after == 258) errors.Add(new TimeoutException(label + " exact process wait timed out."));
            }
            if (job != IntPtr.Zero) ProveJobEmpty(5000, errors);
        }
        private void Collect(Stream pipe, MemoryStream destination) {
            try {
                byte[] bytes = new byte[4096];
                while (true) {
                    int read = pipe.Read(bytes, 0, bytes.Length); if (read == 0) return;
                    bool overflow;
                    lock (captureGate) {
                        overflow = destination.Length + read > limit;
                        if (!overflow) destination.Write(bytes, 0, read);
                    }
                    if (overflow) {
                        Record(new InvalidDataException("Native output overflow; child Job termination required."));
                        if (!TerminateJobObject(job, 197)) Record(new Win32Exception(Marshal.GetLastWin32Error(), "Overflow Job termination"));
                        return;
                    }
                }
            } catch (Exception error) { Record(error); }
        }
        public void Resume() {
            if (disposed || resumed || resumeAttempted) throw new InvalidOperationException("Child can attempt resume exactly once.");
            if (Failed) throw new AggregateException(FailureSnapshot());
            AssertSuspendedOwned();
            runClock.Restart();
            // Commit before the native call: failed or ambiguous return is not
            // permission to retry or restart the original observation clock.
            resumeAttempted = true;
            try {
                uint previous = ResumeThread(thread);
                if (previous == UInt32.MaxValue) Check(false, "Resume assigned child");
                if (previous != 1) throw new InvalidOperationException("Suspended child had an unexpected suspend count.");
                resumed = true;
            } catch (Exception failure) { Record(failure); throw; }
        }
        public void AssertSuspendedOwned() {
            if (disposed || resumed || resumeAttempted) throw new InvalidOperationException("Held suspended child required.");
            if (Failed) throw new AggregateException(FailureSnapshot());
            try {
                bool member;
                Check(IsProcessInJob(process, job, out member), "Verify held Job membership");
                if (!member || WaitForSingleObject(process, 0) != 258)
                    throw new InvalidOperationException("Suspended ownership lost its live process or Job.");
                Accounting accounting;
                Check(QueryInformationJobObject(job, 1, out accounting, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero),
                    "Suspended Job accounting");
                if (accounting.active != 1) throw new InvalidOperationException("Suspended child Job contains another process.");
                // Reject a disproved original count BEFORE any compensating resume.
                // Keeping our increment must not let a second probe acquire admission.
                uint previous = SuspendThread(thread);
                if (previous == UInt32.MaxValue) Check(false, "Inspect held suspend count");
                if (previous != 1) throw new InvalidOperationException("Held suspension was disproved; retain the inspection increment for containment.");
                uint restored = ResumeThread(thread);
                if (restored == UInt32.MaxValue) Check(false, "Restore held suspend count");
                if (restored != 2) throw new InvalidOperationException("Held suspension restoration was ambiguous.");
            } catch (Exception failure) { Record(failure); throw; }
        }
        private void BindObservationLifetime(int maximumLifetimeMilliseconds) {
            if (disposed || !resumed || maximumLifetimeMilliseconds < 1 || maximumLifetimeMilliseconds > 600000)
                throw new InvalidOperationException("A resumed owned child and bounded lifetime are required.");
            if (observationLifetimeMilliseconds == 0) observationLifetimeMilliseconds = maximumLifetimeMilliseconds;
            else if (observationLifetimeMilliseconds != maximumLifetimeMilliseconds)
                throw new InvalidOperationException("Owned child observation lifetime cannot be rebound.");
        }
        private void AssertLiveObservationBoundary() {
            if (completedResult != null || runClock.ElapsedMilliseconds >= observationLifetimeMilliseconds)
                throw new TimeoutException("Owned child is terminal or its original observation lifetime expired.");
            AssertCleanCapture(observationLifetimeMilliseconds);
            uint wait = WaitForSingleObject(process, 0);
            if (wait == UInt32.MaxValue) Check(false, "Exact running child wait");
            if (wait != 258) throw new InvalidOperationException("Exact owned child is not running.");
            // A scheduler delay or newly captured diagnostic during the kernel
            // query must not escape the original live-readiness deadline.
            AssertCleanCapture(observationLifetimeMilliseconds);
        }
        public void AssertCleanCapture(int maximumLifetimeMilliseconds) {
            BindObservationLifetime(maximumLifetimeMilliseconds);
            if (completedResult == null && runClock.ElapsedMilliseconds >= observationLifetimeMilliseconds)
                throw new TimeoutException("Owned child capture exceeded its original observation lifetime.");
            if (Failed) throw new AggregateException(FailureSnapshot());
            lock (captureGate) {
                if (errorBytes.Length != 0)
                    throw new InvalidDataException("Owned child capture contains stderr.");
            }
            // This is deliberately not a root-liveness claim: normal process
            // exit may precede the separately proved Job-empty/collector EOF.
        }
        public void AssertRunningOwned(int maximumLifetimeMilliseconds) {
            BindObservationLifetime(maximumLifetimeMilliseconds);
            AssertLiveObservationBoundary();
            bool member;
            Check(IsProcessInJob(process, job, out member), "Running child Job membership");
            if (!member) throw new InvalidOperationException("Running child lost its exact held Job membership.");
            Accounting accounting;
            Check(QueryInformationJobObject(job, 1, out accounting, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero),
                "Running child Job accounting");
            if (accounting.active < 1) throw new InvalidOperationException("Running child Job has no active process.");
            AssertLiveObservationBoundary();
        }
        public NativeOutputFrame PollFirstOutputLine(int maximumLifetimeMilliseconds, int maximumFrameBytes) {
            if (maximumFrameBytes < 2 || maximumFrameBytes > 8192 || maximumFrameBytes > limit)
                throw new ArgumentException("First output frame byte bound is invalid.");
            AssertRunningOwned(maximumLifetimeMilliseconds);
            if (firstFrameMaximumBytes == 0) firstFrameMaximumBytes = maximumFrameBytes;
            else if (firstFrameMaximumBytes != maximumFrameBytes)
                throw new InvalidOperationException("First output frame byte bound cannot be rebound.");
            NativeOutputFrame candidate = firstOutputFrame;
            if (candidate == null) {
                byte[] frameBytes = null;
                lock (captureGate) {
                    if (errorBytes.Length != 0)
                        throw new InvalidDataException("Owned child emitted stderr before first output frame.");
                    // The collector enforces the overall bound; scan no farther
                    // than this separately pinned first-frame limit before copy.
                    int available = checked((int)outputBytes.Length);
                    int count = Math.Min(available, maximumFrameBytes);
                    byte[] buffer = outputBytes.GetBuffer();
                    for (int index = 0; index < count; index++) {
                        byte value = buffer[index];
                        if (value == 13 || value == 0)
                            throw new InvalidDataException("First output frame contains CR or NUL.");
                        if (value != 10) continue;
                        if (index == 0) throw new InvalidDataException("First output frame is empty.");
                        frameBytes = new byte[index + 1];
                        Buffer.BlockCopy(buffer, 0, frameBytes, 0, frameBytes.Length);
                        break;
                    }
                    if (frameBytes == null && available >= maximumFrameBytes)
                        throw new InvalidDataException("First output frame exceeded its exact LF-inclusive byte bound.");
                }
                if (frameBytes != null) candidate = new NativeOutputFrame(Files.Utf8.GetString(frameBytes),
                    Files.Hash(frameBytes), frameBytes.Length, ProcessId, StartedAt);
            }
            // Pending EOF/descendants are not proof that the root process lives.
            // Even cached frames require fresh exact liveness before returning.
            AssertRunningOwned(maximumLifetimeMilliseconds);
            if (firstOutputFrame == null && candidate != null) firstOutputFrame = candidate;
            return firstOutputFrame;
        }
        public NativeResult PollResult(int maximumLifetimeMilliseconds) {
            BindObservationLifetime(maximumLifetimeMilliseconds);
            if (completedResult != null) return completedResult;
            if (runClock.ElapsedMilliseconds >= maximumLifetimeMilliseconds || Failed) {
                if (runClock.ElapsedMilliseconds >= maximumLifetimeMilliseconds)
                    Record(new TimeoutException("Owned child lifetime expired during nonblocking supervision."));
                List<Exception> containment = new List<Exception>();
                StopOwnedProcessAndJob(containment, "Nonblocking child containment");
                foreach (Exception failure in containment) Record(failure);
                throw new AggregateException(FailureSnapshot());
            }
            uint wait = WaitForSingleObject(process, 0);
            if (wait == 258) return null;
            if (wait != 0) {
                Check(wait != UInt32.MaxValue, "Nonblocking exact child wait");
                throw new InvalidOperationException("Unknown nonblocking child wait result: " + wait);
            }
            Accounting accounting;
            Check(QueryInformationJobObject(job, 1, out accounting, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero),
                "Nonblocking Job descendant proof");
            if (accounting.active != 0 || outputThread.IsAlive || errorThread.IsAlive) return null;
            // Join completed collectors for a memory barrier before touching their buffers.
            if (!outputThread.Join(0) || !errorThread.Join(0)) return null;
            if (Failed) throw new AggregateException(FailureSnapshot());
            uint code;
            Check(GetExitCodeProcess(process, out code), "Nonblocking exit code");
            if (runClock.ElapsedMilliseconds >= maximumLifetimeMilliseconds)
                throw new TimeoutException("Owned tree/EOF completion exceeded its original lifetime.");
            completedResult = new NativeResult { ExitCode = unchecked((int)code), ProcessId = ProcessId, StartedAt = StartedAt,
                Stdout = Files.Utf8.GetString(outputBytes.ToArray()), Stderr = Files.Utf8.GetString(errorBytes.ToArray()),
                StdoutBytes = outputBytes.Length, StderrBytes = errorBytes.Length };
            return completedResult;
        }
        private bool ProveJobEmpty(int milliseconds, List<Exception> errors) {
            Stopwatch clock = Stopwatch.StartNew();
            while (true) {
                Accounting accounting;
                if (!QueryInformationJobObject(job, 1, out accounting, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero)) {
                    errors.Add(new Win32Exception(Marshal.GetLastWin32Error(), "Job descendant proof")); return false;
                }
                if (accounting.active == 0) return true;
                if (clock.ElapsedMilliseconds >= milliseconds) {
                    errors.Add(new TimeoutException("Job still has active descendants after bounded termination.")); return false;
                }
                Thread.Sleep(10);
            }
        }
        public NativeResult Wait(int timeoutMilliseconds) {
            if (disposed || !resumed || timeoutMilliseconds < 1 || timeoutMilliseconds > 300000)
                throw new InvalidOperationException("A resumed child and bounded wait are required.");
            if (observationLifetimeMilliseconds != 0)
                throw new InvalidOperationException("Blocking wait cannot replace an already-bound observation lifetime.");
            Stopwatch clock = Stopwatch.StartNew();
            while (true) {
                uint wait = WaitForSingleObject(process, 20);
                if (wait == 0) break;
                if (wait != 258) {
                    Record(wait == UInt32.MaxValue ? (Exception)new Win32Exception(Marshal.GetLastWin32Error(), "Native process wait") :
                        new InvalidOperationException("Unknown native process wait result: " + wait));
                    if (!TerminateJobObject(job, 198)) Record(new Win32Exception(Marshal.GetLastWin32Error(), "Wait-failure Job termination"));
                    break;
                }
                if (Failed || clock.ElapsedMilliseconds >= timeoutMilliseconds) {
                    if (clock.ElapsedMilliseconds >= timeoutMilliseconds) Record(new TimeoutException("Native wall-clock deadline exceeded."));
                    if (!TerminateJobObject(job, 198)) Record(new Win32Exception(Marshal.GetLastWin32Error(), "Bounded Job termination"));
                    break;
                }
            }
            List<Exception> secondaryWaitErrors = new List<Exception>();
            uint finalWait = ClassifiedWait(process, 5000, "Native final process wait", secondaryWaitErrors);
            if (finalWait != 0) {
                if (finalWait == 258) secondaryWaitErrors.Add(new TimeoutException("Native child did not terminate."));
                StopOwnedProcessAndJob(secondaryWaitErrors, "Secondary wait containment");
            }
            foreach (Exception waitError in secondaryWaitErrors) Record(waitError);
            uint code = 0; if (!GetExitCodeProcess(process, out code)) Record(new Win32Exception(Marshal.GetLastWin32Error(), "Native exit code"));
            // A signaled primary process can precede the Job accounting update.
            // Descendants may also finish normally. They and both EOF collectors
            // share the REMAINDER of the original wall-clock deadline, not a fresh
            // grace window. No success is possible while any Job process remains.
            List<Exception> descendantErrors = new List<Exception>();
            int remaining = (int)Math.Max(0L, timeoutMilliseconds - clock.ElapsedMilliseconds);
            bool treeCompleted = ProveJobEmpty(remaining, descendantErrors);
            foreach (Exception descendantError in descendantErrors) Record(descendantError);
            if (!treeCompleted) {
                List<Exception> containmentErrors = new List<Exception>();
                StopOwnedProcessAndJob(containmentErrors, "Deadline descendant containment");
                foreach (Exception containmentError in containmentErrors) Record(containmentError);
            }
            remaining = (int)Math.Max(0L, timeoutMilliseconds - clock.ElapsedMilliseconds);
            if (outputThread != null && !outputThread.Join(remaining)) Record(new TimeoutException("stdout EOF exceeded the shared deadline."));
            remaining = (int)Math.Max(0L, timeoutMilliseconds - clock.ElapsedMilliseconds);
            if (errorThread != null && !errorThread.Join(remaining)) Record(new TimeoutException("stderr EOF exceeded the shared deadline."));
            if (clock.ElapsedMilliseconds >= timeoutMilliseconds)
                Record(new TimeoutException("Native tree/EOF completion exceeded the original shared deadline."));
            if (Failed) throw new AggregateException(FailureSnapshot());
            return new NativeResult { ExitCode = unchecked((int)code), ProcessId = ProcessId, StartedAt = StartedAt,
                Stdout = Files.Utf8.GetString(outputBytes.ToArray()), Stderr = Files.Utf8.GetString(errorBytes.ToArray()),
                StdoutBytes = outputBytes.Length, StderrBytes = errorBytes.Length };
        }
        public static string Quote(string value) {
            if (value == null || value.IndexOf('\0') >= 0 || value.IndexOf('\r') >= 0 || value.IndexOf('\n') >= 0)
                throw new ArgumentException("Native token contains a control character.");
            StringBuilder result = new StringBuilder("\""); int slashes = 0;
            foreach (char c in value) {
                if (c == '\\') { slashes++; continue; }
                if (c == '"') result.Append('\\', slashes * 2 + 1).Append(c);
                else result.Append('\\', slashes).Append(c);
                slashes = 0;
            }
            return result.Append('\\', slashes * 2).Append('"').ToString();
        }
        public void StopAndProve() {
            if (disposed) throw new ObjectDisposedException("NativeChild");
            List<Exception> errors = new List<Exception>();
            StopOwnedProcessAndJob(errors, "Owned observer containment");
            if (outputThread != null && !outputThread.Join(5000)) errors.Add(new TimeoutException("Owned stdout EOF containment."));
            if (errorThread != null && !errorThread.Join(5000)) errors.Add(new TimeoutException("Owned stderr EOF containment."));
            // Retain native handles on failure so containment can retry and never
            // mistake a disposed wrapper for a proved-empty process tree.
            if (errors.Count != 0) throw new AggregateException(errors.ToArray());
        }
        public void Dispose() {
            if (disposed) return; disposed = true;
            List<Exception> cleanup = new List<Exception>();
            // Also covers failed membership/identity checks before child resume.
            StopOwnedProcessAndJob(cleanup, "Cleanup containment");
            if (outputThread != null && outputThread.IsAlive && !outputThread.Join(5000)) cleanup.Add(new TimeoutException("Cleanup stdout join."));
            if (errorThread != null && errorThread.IsAlive && !errorThread.Join(5000)) cleanup.Add(new TimeoutException("Cleanup stderr join."));
            foreach (IDisposable item in new IDisposable[] { input,output,error,outputBytes,errorBytes }) {
                if (item == null) continue;
                try { item.Dispose(); } catch(Exception failure) { cleanup.Add(failure); }
            }
            foreach (IntPtr handle in new IntPtr[] { thread,process,job }) {
                if (handle != IntPtr.Zero && !CloseHandle(handle)) cleanup.Add(new Win32Exception(Marshal.GetLastWin32Error(), "Close native handle"));
            }
            if (cleanup.Count != 0) throw new AggregateException(cleanup.ToArray());
        }
    }
}
