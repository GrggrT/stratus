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
            if (bytes.Length == 0 || bytes.Length > 1048576)
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
        private readonly List<HeldFile> held = new List<HeldFile>();
        private readonly object collectionGate = new object();
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
        private IndependentWatchdogLaunch(string executable,string executableHash,string source,string sourceHash,
                string runtime,string runtimeHash,string contract,string contractHash,string sessionId,string leasePath,
                string readyPath,string directory,IDictionary<string,string> environment,int startupMilliseconds,int maximumBytes) {
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
                // BREAKAWAY_FROM_JOB | NO_WINDOW | EXTENDED_STARTUPINFO | UNICODE_ENVIRONMENT | SUSPENDED.
                Check(CreateProcess(executable,command,IntPtr.Zero,IntPtr.Zero,true,0x09080404,
                    environmentMemory,directory,ref startup,out created),"Create independent watchdog suspended");
                process=created.process; thread=created.thread; ProcessId=created.pid;
                long started,exited,kernel,user;
                Check(GetProcessTimes(process,out started,out exited,out kernel,out user),"Independent creation time");
                StartedAt=new DateTimeOffset(DateTime.FromFileTimeUtc(started)).ToString("o");
                AssertIdentity(); identityEstablished=true; AssertNoJob();
                output.DisposeLocalCopyOfClientHandle(); error.DisposeLocalCopyOfClientHandle();
                input.DisposeLocalCopyOfClientHandle(); input.Dispose(); input=null;
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
                uint restored=ResumeThread(thread); Check(restored!=UInt32.MaxValue,"Independent suspend restoration");
                if(previous!=1 || restored!=2) throw new InvalidOperationException("Independent child is not exactly once suspended.");
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
            if(!resumeSucceeded) throw new InvalidOperationException("Independent watchdog has no successful owned Resume.");
            try {
                if(!IsAlive()) throw new InvalidOperationException("Independent watchdog exited before a live readiness proof.");
                AssertIdentity(); AssertNoJob(); ThrowFaults();
                byte[] bytes;
                lock(collectionGate) {
                    if(outputEof || errorEof) throw new EndOfStreamException("Independent startup pipe reached EOF while watchdog is live.");
                    bytes=outputBytes.ToArray();
                }
                if(readiness!=null) {
                    lock(collectionGate) {
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
                    AssertCollectedFrame(frame.Length);
                    if(!IsAlive()) throw new InvalidOperationException("Independent watchdog exited before readiness publication.");
                    acceptedFrameBytes=frame.Length; readiness=new Readiness(this,leaseHash,readyHash,frame); return readiness;
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
                // Restore only our inspection increment, never resume an originally
                // suspended child to execute. Unexpected count means execution may
                // already have occurred outside the wrapper; retain the capability.
                uint restored=ResumeThread(thread); resumeAttempted=true;
                Exception failure=new InvalidOperationException("Abort lacks an exact native never-resumed suspend proof.");
                if(restored==UInt32.MaxValue) failure=Files.Combine(failure,new Win32Exception(Marshal.GetLastWin32Error(),"Restore abort inspection increment"));
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

    public sealed class NativeResult {
        public int ExitCode;
        public string Stdout, Stderr;
        public int ProcessId;
        public string StartedAt;
        public long StdoutBytes, StderrBytes;
    }

    // A child starts suspended, is assigned to a kill-on-close Job, and only then
    // resumes. The Job remains held until every descendant is gone. No Start/Assign
    // window, no recursive PID discovery, and no unbounded whole-stream reader.
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
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
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
        private readonly List<Exception> failures = new List<Exception>();
        private int limit;
        private bool resumed,disposed;
        private readonly Stopwatch runClock = new Stopwatch();
        private NativeResult completedResult;
        public readonly int ProcessId;
        public readonly string StartedAt;
        private NativeChild(string executable, string[] arguments, string directory, int maximumBytes) {
            limit = maximumBytes;
            if (limit < 1 || limit > 1048576) throw new ArgumentException("Invalid output byte bound.");
            IntPtr attributes = IntPtr.Zero, handles = IntPtr.Zero;
            bool initialized = false;
            try {
                job = CreateJobObject(IntPtr.Zero, null); Check(job != IntPtr.Zero, "CreateJobObject");
                ExtendedLimit limits = new ExtendedLimit(); limits.basic.flags = 0x2000;
                Check(SetInformationJobObject(job, 9, ref limits, (uint)Marshal.SizeOf(limits)), "Job kill-on-close");
                output = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
                error = new AnonymousPipeServerStream(PipeDirection.In, HandleInheritability.Inheritable);
                input = new AnonymousPipeServerStream(PipeDirection.Out, HandleInheritability.Inheritable);
                IntPtr size = IntPtr.Zero;
                InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
                if (size == IntPtr.Zero) throw new Win32Exception(Marshal.GetLastWin32Error(), "Attribute size");
                attributes = Marshal.AllocHGlobal(size);
                Check(InitializeProcThreadAttributeList(attributes, 1, 0, ref size), "Attribute list"); initialized = true;
                handles = Marshal.AllocHGlobal(IntPtr.Size * 3);
                Marshal.WriteIntPtr(handles, 0, input.ClientSafePipeHandle.DangerousGetHandle());
                Marshal.WriteIntPtr(handles, IntPtr.Size, output.ClientSafePipeHandle.DangerousGetHandle());
                Marshal.WriteIntPtr(handles, IntPtr.Size * 2, error.ClientSafePipeHandle.DangerousGetHandle());
                Check(UpdateProcThreadAttribute(attributes, 0, new IntPtr(0x20002), handles,
                    new IntPtr(IntPtr.Size * 3), IntPtr.Zero, IntPtr.Zero), "Explicit inherited handle list");
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
                Check(AssignProcessToJobObject(job, process), "Assign suspended process to Job");
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
                    if (destination.Length + read > limit) {
                        Record(new InvalidDataException("Native output overflow; child Job termination required."));
                        if (!TerminateJobObject(job, 197)) Record(new Win32Exception(Marshal.GetLastWin32Error(), "Overflow Job termination"));
                        return;
                    }
                    destination.Write(bytes, 0, read);
                }
            } catch (Exception error) { Record(error); }
        }
        public void Resume() {
            if (disposed || resumed) throw new InvalidOperationException("Child can resume exactly once.");
            if (Failed) throw new AggregateException(FailureSnapshot());
            AssertSuspendedOwned();
            runClock.Restart();
            uint previous = ResumeThread(thread);
            if (previous == UInt32.MaxValue) Check(false, "Resume assigned child");
            if (previous != 1) throw new InvalidOperationException("Suspended child had an unexpected suspend count.");
            resumed = true;
        }
        public void AssertSuspendedOwned() {
            if (disposed || resumed) throw new InvalidOperationException("Held suspended child required.");
            bool member;
            Check(IsProcessInJob(process, job, out member), "Verify held Job membership");
            if (!member || WaitForSingleObject(process, 0) != 258)
                throw new InvalidOperationException("Suspended ownership lost its live process or Job.");
            Accounting accounting;
            Check(QueryInformationJobObject(job, 1, out accounting, (uint)Marshal.SizeOf(typeof(Accounting)), IntPtr.Zero),
                "Suspended Job accounting");
            if (accounting.active != 1) throw new InvalidOperationException("Suspended child Job contains another process.");
            // Observe the kernel's prior suspend count, then undo only our increment.
            // A caller-supplied 'suspended' flag is never a process-state proof.
            uint previous = SuspendThread(thread);
            if (previous == UInt32.MaxValue) Check(false, "Inspect held suspend count");
            uint restored = ResumeThread(thread);
            if (restored == UInt32.MaxValue) Check(false, "Restore held suspend count");
            if (previous != 1 || restored != 2)
                throw new InvalidOperationException("Held thread was not exactly once suspended.");
        }
        public NativeResult PollResult(int maximumLifetimeMilliseconds) {
            if (disposed || !resumed || maximumLifetimeMilliseconds < 1 || maximumLifetimeMilliseconds > 600000)
                throw new InvalidOperationException("A resumed owned child and bounded lifetime are required.");
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
            // Also covers assignment failure while the new child is still suspended.
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
