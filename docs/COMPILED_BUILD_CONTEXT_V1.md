# Compiled build context v1 — unqualified, nonauthorizing metadata

This specifies the shared `ContextMetadata.ParseAndDerive(byte[])` source in
`scripts/Stratus.RuntimeBuild.ContextMetadata.cs`. It is a BCL-only parser and
metadata derivation for the independently pinned P/W closure. It does not open
files, invoke native APIs, prepare an actual input owner, execute a compiler, or
approve a candidate. Its result is never an approval token or a retained lease.
No source compilation, semantic execution, native qualification, or R0–R9
acceptance is established by this document or by source inspection.

## Outer envelope

The input is the receiver's actual private captured byte array, cloned before
parsing. The maximum outer size is 8,388,608 bytes. The envelope is strict UTF-8,
without BOM, compact canonical JSON followed by exactly one LF. The following
fields are mandatory, exclusive, case-sensitive, and in this exact order:

```text
format, root_id, root, revision, build_id, build_worktree, build_directory,
profile, plan, manifest
```

`format` is exactly `stratus-runtime-build-context-v1`. `root_id` is a declared
identifier matching `[a-z0-9][a-z0-9.-]{0,63}`. It is not a selected trust root.
`revision` has 40 lowercase hexadecimal characters; `build_id` has 32.
`root`, `build_worktree`, and `build_directory` are bounded canonical drive-local
paths. Root and worktree must be different and neither may be nested inside the
other, comparing paths case-insensitively. The directory is exactly:

```text
<root>\.stratus-cache\runtime-builds\<build_id>.in-progress
```

Each of `profile`, `plan`, and `manifest` is exactly this ordered object:

```text
path, sha256, bytes_base64
```

`sha256` has 64 lowercase hexadecimal characters and must equal SHA256 of the actual decoded
component bytes. Base64 is the canonical standard alphabet with required padding,
no whitespace, and no alternate pad bits. Profile and plan each decode to 1–262,144
bytes; manifest decodes to 1–4,194,304 bytes. Encoded lengths and predicted decoded
lengths are checked before decoding allocates the component byte array. The exact
component bytes are privately cloned and retained for future correspondence;
accessors return defensive copies only. Paths and the envelope's scope are all
untrusted declarations until checked by a genuinely independent root.

No embedded source, DLL, reference, executable, environment, writer, deadline,
approval flag, or arbitrary payload field is permitted. Only the two metadata
documents and source-manifest metadata are decoded from this carrier.

## Canonical JSON and parser bounds

The parser accepts only objects, arrays, strings, and nonnegative Int64 tokens.
All accepted schema integer fields are additionally positive and bounded. Null,
booleans, negative numbers, floats, exponents, leading zeros, whitespace, comments,
and trailing data are rejected. These omitted types cannot form a successful
legacy v2 profile or plan either.

Strings escape quotation mark as `\"`, backslash as `\\`, and code points below
U+0020 as four-digit lowercase `\u00xx`. Other characters are literal strict
UTF-8. Alternate JSON spellings such as `\/`, `\t`, or printable `\uXXXX` reject.
Ordinary schema strings have 1–32,766 UTF-16 code units and forbid NUL, CR, and LF.
Keys have at most 64 code units. Each document has at most 4,096 value nodes, depth
at most 8 with root depth 0, at most 32 members per object, and at most 128 items per
array. Duplicate keys, unknown keys, missing keys, and wrong types reject.
Outer string tokens are also bounded by the maximum manifest base64 length
(5,592,408 code units); individual fields apply their tighter limits afterward.
Embedded profile/plan string tokens are bounded at 32,766 before expansion.

Legacy profile/plan property sets are exact but their property order is **not**
fixed. The reader enforces canonical tokens directly while retaining the observed
order; it does not sort properties or impose the fixture's historical ordering.
The new envelope and its three wrappers, unlike embedded v2, have fixed ordering.

All paths use the existing stricter owned-input lexical boundary: 4–260 UTF-16
code units, ASCII drive letter plus `:\`, no extra colon, forward slash, empty,
`.` or `..` component, trailing component dot/space, control character, DEL,
Windows wildcard/invalid filename character, listed DOS device stem, or
unpaired surrogate. Device stems `CON`, `PRN`, `AUX`, `NUL`, and `COM`/`LPT`
followed by ASCII 1–9 or superscript 1–3 are checked case-insensitively after trimming
spaces before the extension. This deliberately retains the eventual CLR owner's
stricter path bounds rather than the legacy PowerShell preflight's 32766 limit.
The envelope and the actual owner's lexical helper reject all three superscript forms. These six
superscript device names are reserved under the documented Windows naming rules:
[Microsoft naming conventions](https://learn.microsoft.com/ru-ru/windows/win32/fileio/naming-a-file).
This alignment is a source-level change, not a filesystem/native qualification.
These lexical rules do not establish physical identity, absence of links,
filesystem provenance, ancestry ownership, or exclusive access.

## Embedded build profile v2

Exact fields, in any property order:

```text
format, profile_id, host, compiler, references, target_framework,
architecture, assembly_name, producer, metadata_verifier, dependencies,
compiler_recipe
```

The fixed values are `stratus-parent-command-build-profile-v2`, architecture
`x64`, assembly name `Stratus.ParentStage.Runtime`, and compiler recipe
`direct-csc-exe-v1`. The profile identifier uses the same grammar as `root_id`.
Target framework matches
`\.NET(Framework|CoreApp|Standard),Version=v[0-9]+\.[0-9]+(\.[0-9]+)?`.

A file record is exactly `path,sha256,bytes`, with positive bounded byte count.
Host has exactly `path,sha256,bytes,ps_edition,ps_version,clr_version`; compiler
has exactly `path,sha256,bytes,version`. Both paths end `.exe` case-insensitively
and are bounded at 134,217,728 bytes. Host edition is `Desktop` with version
`5.1.<digits>.<digits>`, or `Core` with `7.<digits>.<digits>[.<digits>]`.
CLR version has 2–4 numeric components. Compiler version is an ordinary string,
not a verified compiler identity or compatibility determination.

Producer and metadata verifier are file records bounded at 16,777,216 bytes.
References are an ordered array of 1–32 file records, each bounded at 134,217,728
bytes and ending `.dll` case-insensitively. Dependencies are an ordered array
of 1–64 records, each exactly `role,path,sha256,bytes`, with the same 134,217,728
limit. Roles are exactly the 12 combinations of
`{host,compiler,producer,verifier}_{runtime,dependency,config}`. Repeated roles
are permitted; repeated paths are not. V2 does not require every role category
or establish actual dependency-resolution closure.

## Embedded source manifest

The manifest is strict UTF-8 without BOM/NUL/CR, with a final LF, and 1–16,384
records. Each record has exactly three tab-separated fields:

```text
relative-path<TAB>positive-byte-count<TAB>lowercase-sha256<LF>
```

Relative paths match `[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*`, are strictly increasing
ordinally, and are unique case-insensitively. Every path joined to worktree also
passes the strict path rule. Byte counts have 1–19 digits, no leading zero, and
fit positive Int64. There is exactly one exact-case runtime entry
`scripts/Stratus.ParentStage.Runtime.cs`; its length is 1–2,097,152. Its source
path, length, and hash are derived from these actual manifest bytes. Other
entries are syntactically validated, not opened or added to the build inventory;
their declared lengths are not summed and do not prove checkout contents.

## Embedded build plan v2 and binding

The exact plan fields, in any property order, are:

```text
format, build_id, revision, source_manifest_sha256, profile_sha256,
build_worktree, build_directory, source, output, compiler_arguments
```

Format is `stratus-parent-command-build-plan-v2`. Build ID, revision, worktree,
and directory must equal the outer declarations exactly. Profile and manifest
hashes equal the corresponding decoded component hashes. Source is an exact file
record equal to the manifest-derived runtime entry. Output is exactly `{path}`
with `<build_directory>\Stratus.ParentStage.Runtime.dll`. The plan wrapper path
is exactly `<build_directory>\command-runtime-plan.json`.

Compiler arguments must be exactly ordered as `/target:library`, `/noconfig`,
`/nostdlib+`, `/platform:x64`, `/out:<output>`, each `/reference:<path>` in profile
order, then the manifest-derived source path. No optional flags, response files,
hosted compiler, alternate output/source, or extra arguments are allowed. The
legacy preliminary count bound is 7–39; this exact recipe accepts 7–38 arguments.

## Derived nonauthorizing inventory

The copied immutable declarations contain path, hash, bytes, and fixed maximum.
Their order and limits are:

| Order | Role | Maximum bytes |
| --- | --- | ---: |
| 0 | Actual profile document | 262144 |
| 1 | Actual plan document | 262144 |
| 2 | Actual source manifest | 4194304 |
| 3 | Manifest-derived runtime source | 2097152 |
| 4 | Host | 134217728 |
| 5 | Compiler | 134217728 |
| 6 | Producer | 16777216 |
| 7 | Metadata verifier | 16777216 |
| Following | References in declaration order | 134217728 each |
| Following | Dependencies in declaration order | 134217728 each |

Declared executable/tool/reference/dependency inputs contain 6–100 entries and
sum to at most 268,435,456 bytes. The complete inventory contains 10–104 entries
and sums to at most 275,251,200 bytes. Every path, including all three documents,
source, declared inputs, and the **excluded output**, must be distinct under
OrdinalIgnoreCase. Array/record copies grant neither handle provenance nor
permission to instantiate or open an actual owner.

## Independent approval and remaining boundaries

The result exposes ContextSha256, computed over its own clone of the complete
captured envelope, and declared RootId/Root/Revision/BuildId/BuildWorktree/
BuildDirectory/ProfileSha256/PlanSha256/ManifestSha256 for future exact descriptor
comparison. Caller-selected pins, this envelope's own hashes, adjacent JSON,
Expected-like declarations, and HELLO/READY/nonces cannot independently approve
themselves. Old v2's plan wrapper digest proved only raw-byte correspondence;
there was no independent `Expected.plan_sha256`. The complete-envelope digest
also binds document paths and declared scope; component hashes alone do not.
A real independent root must
select the intended exact context and qualification scope outside the candidate.
Until that root exists and is qualified, deny-all selection and current entry
blockers remain mandatory. This parser does not install or authenticate a root.

P/W context checks occur only after their executable/runtime code has loaded.
They cannot authorize that earlier preload, especially for W running from birth.
The actual L-side launch factories now require the same retained StartupPreloadOwner,
whose separate private descriptor selector remains null and whose preload Open
has its own unconditional qualification blocker. This descriptor concerns exact
declared P/W layouts and launch values, not the v2 payload inventory above.
Actual captured preload snapshots must be checked before borrower registration;
neither declared files nor four environment keys prove complete load resolution,
absence of injected configuration, or the already-loaded L/host/bootstrap scope.
The startup owner does not supply ContextTrust's missing independent approval.

Legacy v2 does not qualify P/W/bootstrap binaries or preloads, dependency
resolution, actual environment, exclusive output writers, mapping writable-view
closure, clean checkout, fresh build directory, original deadlines, W supervision,
physical containment, compiler causality, metadata compatibility, receipt/import,
production, or any R0–R9 gate. Those requirements must not be added as optional v2
fields or represented by caller booleans. Source/DLL/reference payload stays out
of the metadata carrier.

The compiled P partial input coordinator now derives and retains a managed-only,
unopened owner from its original admitted capture; it accepts no caller inventory
or approval flag. Entry/control v3 and W launch/watch v5 bind an explicit private
W admission ACK to the existing identity, context and original clock fields.
The ACK follows W's observation of the exact P HELLO; sending the P reply alone
does not establish receipt. READY does not wait for P HELLO, since the future
first P continuation must precede that handshake. An ACK is only an observation,
not sustained W supervision, Job/root acceptance or permission to open payload.

The P worker contains a separate unconditional payload-Open blocker. Installing
a trust descriptor alone cannot enable Open. The authored path after that blocker
uses snapshots from the retained exact owner, validates actual path/length/hash
and exact profile/plan/manifest bytes against the admitted metadata, then checks
AssertHeld before publishing completion. That path has not run or been compiled;
all native behavior and the qualified full build closure remain unaccepted.
W closes new control admission at STOP entry; already in-flight writes are not
claimed to be atomically revoked. Its main loop never waits for control writes.

CompilerCommand's source lifecycle now requires retained `PrepareBirthJob()` before
`StartSuspended()` (managed ABI v2). The actual L-side P wrapper uses both phases
under its original deadline. The prepared Job is P's outer birth Job, not a W-held
compiler Job or an ACK. Creation/configuration outcomes are retained independently;
birth requires the same known-owned configured Job, fresh zero accounting including
total/terminated, no prior birth attempt, and refreshed cwd/executable ancestry.
Configuration failure blocks birth but preserves known-created ownership for explicit
containment. Unknown creation/close cannot authorize native use or replacement.
Source separation does not qualify filesystem writer isolation or native behavior.

The current unqualified integration moves the actual original entry, clock,
identity, private reader-owned ACK and verified input phase into one retained
ProducerInvocation. A retained compiler owner uses that same invocation, not a
new Stopwatch or caller-provided snapshot. The compiler role is retained explicitly
by the strict parser. A separate private zero-argument environment selector stays
null; process environment and path-derived system roots are not approval sources.
The dedicated command is retained before borrower registration; unknown outcomes
cannot authorize native Job preparation or borrower release. A separate compiler
Job-preparation blocker stays unconditional, and generic birth/Resume reject this
mode. Independent source review found no blocking issue; ten pinned source/BCL
suites passed 1020 cases on each of PS5/PS7 at their first attempts. All 946 prior
case IDs/order remain, plus 74 focused cases. The inherited clock lock covers only
the original clock/fault operation, not payload IO; no hard scheduling or lock-free
claim follows from source inspection. No C# compilation/execution or runtime/native
qualification has been established. Evidence is under
`.stratus-cache/producer-compiler-owner-20260923`.

The completed code-only transfer checkpoint uses entry/control v4
and W launch/watch v6. A distinct original-P transfer capability requests only
PROCESS_DUP_HANDLE (0x40); the existing stop capability remains 0x101001. This is
process-table-wide authority, not Job-only authority. Its acquisition has an
additional unconditional qualification blocker. CompareObjectHandles is a new
independently qualified boundary; no PID reopen or fallback is permitted.

The actual P command irrevocably pins its original Job slot before its private
writer may publish an offer. Its actual private reader must receive the exact
bounded ACK for original nonce/identity/ticks/context/Job. A separate compiler-birth
blocker remains closed after this observation. W must hold its own duplicate
before ACK, arbitrate duplication against STOP, and retain unknown outcomes.
STOP closes new admission, establishes P physical death/no future births, resolves
any claimed duplication, then terminates and rechecks the exact compiler Job.
P death alone or earlier empty accounting is insufficient while W may hold a Job.
Root ACK and full terminal are subsequent source work, not implied by Job ACK.
Nested Jobs require qualification without breakaway or an assumption that P's
outer Job always has exactly one process. No native behavior is claimed here.

Independent source review and a separate directed-coverage audit were completed;
twelve independently safety-reviewed, pinned suites passed on PS5/PS7 at their
first attempts (1286 cases each). All 1178 prior case arrays/order were preserved,
with 108 focused transfer cases added. P's reader-gap source correction and two
pre-execution predicate-anchor corrections are recorded before the runs. Tests
only inspect source/BCL data; no C#/native execution or qualification follows.
Evidence and pending qualification questions are under
`.stratus-cache/guardian-compiler-job-transfer-20260923`.

Full post-Open same-owned-byte validation and AssertHeld remain required under
separately qualified W supervision. W main must not block on metadata parsing; parsing cannot restart or
replace the original deadline. Guarded first P continuation, private handshake,
compiler Job/root transfer and real ACK ordering, and complete external P/W exit
and EOF terminal joins remain separate integration requirements. W's current stop
capability must not silently gain rights, and P/W cleanup must not introduce a
mutual wait cycle. Pre-W L stalls are not bounded by a timestamp or a W that can
stop only P. Preload, writer, inheritance-isolation, and native qualification are
still separately scoped. Existing 15/25-second thresholds, PAPER canary terminal
STOP, production permissions, LIVE prohibition, and Binance kill switch remain
unchanged. No native execution, compilation, Sandbox, CI dispatch, Docker/DB/
service action, publication, or production start is authorized by this source.
