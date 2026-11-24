# Source-only native Windows x64 portable smoke

This is an operator-run **synthetic acceptance fixture**, not an installer,
production migration, network service, Windows runner request, or WebUI
test. It requires an already authorized disposable native Windows x64
machine, x64 CPython 3.12+, and PowerShell 7.3+ installed in an operator
trusted location subject to the runner's ordinary execution policies.
No run is asserted by this source change; Linux cannot qualify it.

## Prepare the exact artifact

Use a `lmh-portable-core-v1` extracted Windows x64 tree and a separately
trusted Hub verifier executable. Obtain independent expected SHA-256 pins
for the verifier, manifest, packaged launcher and candidate binary, plus
exact source revision. Do not fill in expected values merely by hashing
or querying the unverified package. The native `verify-portable-core`
command checks the exact manifest inventory and PE32+ x64 executable form;
the harness then checks that verification's receipt against your pins.
It needs the manifest-producing portable staging command, not the
unmanifested convenience `packaging/windows/stage-core.ps1` alone.

Provide only trusted local fixed NTFS paths and protect every ancestor.
Prepare the package and a scratch parent in private locations accessible
to the current test account. The harness rejects reparse-point components,
nonordinary/UNC paths, non-NTFS volumes, and unexpected file types. It
checks that the current user owns the inspected ACL leaves and rejects
allow ACEs outside the current user, Local System and built-in
Administrators. This is a conservative check, not an effective-rights or
ancestor ACL audit, and does not change ACLs. If it refuses your runner
setup, do not weaken the check or override local policy. No credentials
are taken as arguments or sent to a provider.

Run these modes **separately**, using absolute paths from that runner:

```powershell
python .\scripts\smoke-windows-portable.py --mode foreground --pwsh $TrustedPwsh `
  --verifier $TrustedVerifier --verifier-sha256 $VerifierPin --package $ExtractedPackage `
  --manifest-sha256 $ManifestPin --binary-sha256 $BinaryPin --wrapper-sha256 $WrapperPin `
  --source-revision $SourceCommit --private-scratch $PrivateScratch

python .\scripts\smoke-windows-portable.py --mode managed --pwsh $TrustedPwsh `
  --verifier $TrustedVerifier --verifier-sha256 $VerifierPin --package $ExtractedPackage `
  --manifest-sha256 $ManifestPin --binary-sha256 $BinaryPin --wrapper-sha256 $WrapperPin `
  --source-revision $SourceCommit --private-scratch $PrivateScratch
```

Each creates its own empty ordinary archive and separate grant file.
Local loopback checks require healthy readiness, missing/incorrect bearer
rejection, exact-account results despite another account's same raw ID,
and denial of foreign access. An independent synthetic producer provides the
fixture rows; the Desktop read grant must not admit those writes.

- **Foreground** runs the packaged PowerShell `run-core.ps1 -Mode Live`
  with no managed protocol. The harness assigns the gated wrapper to an
  unnamed test-only Job Object before the wrapper runs the candidate.
  It uses that Job for isolated teardown, checks the Job is empty and
  the loopback listener closed, and does **not** infer interactive
  Ctrl+C/graceful shutdown or product Job Object support. A disposable
  runner that does not permit the nested test Job is unsupported.
- **Managed** calls the candidate's `desktop-supervise --live` directly.
  Such stdio pipes are anonymous and privately owned by construction;
  the candidate's `GetFileType` test alone does not authenticate peers
  or distinguish them from named pipes. The harness verifies startup and
  the actual child lifetime through a wait-only process handle taken at
  readiness. Separate launches cover LF `stop\n` and standalone parent
  stdin EOF (exact clean `forced:false`), plus an invalid outer command
  (nonzero/no clean frame). Each checks backend exit and closed listener.
  No test Job Object wraps the managed lane. The test does not establish
  arbitrary forced process-tree termination or lost-write rollback.

Record exact manifest/binary pins, build target, Windows version,
each mode's result and any cleanup failures. Native execution, signed
packaging/dependencies, interactive console behavior, stock WebUI, real
account/device integration, suspended or hung workers, in-flight writes
and SSE drain require separate evidence. Neither mode runs those lanes.
