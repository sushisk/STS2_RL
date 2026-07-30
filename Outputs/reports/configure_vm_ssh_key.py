from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    pub_path = Path.home() / ".ssh" / "id_rsa.pub"
    public_key = pub_path.read_text(encoding="utf-8").strip()
    public_key_b64 = base64.b64encode(public_key.encode("utf-8")).decode("ascii")
    command = (
        "powershell -ExecutionPolicy Bypass -Command "
        f"$k=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{public_key_b64}')); "
        "$paths=@('C:\\ProgramData\\ssh\\administrators_authorized_keys','C:\\Users\\azureuser\\.ssh\\authorized_keys'); "
        "foreach($p in $paths){"
        "New-Item -ItemType Directory -Force -Path (Split-Path $p -Parent)|Out-Null; "
        "if(!(Test-Path $p)){New-Item -ItemType File -Force -Path $p|Out-Null}; "
        "$existing=Get-Content -LiteralPath $p -Raw -ErrorAction SilentlyContinue; "
        "if($existing -notmatch [regex]::Escape($k)){Add-Content -LiteralPath $p -Value $k -Encoding ascii}; "
        "icacls $p /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null"
        "}; "
        "Restart-Service sshd"
    )
    settings = {"commandToExecute": command}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(settings, f)
        settings_path = f.name
    try:
        az_exe = shutil.which("az") or shutil.which("az.cmd") or r"C:\Program Files (x86)\Microsoft SDKs\Azure\CLI2\wbin\az.cmd"
        subprocess.run(
            [
                az_exe,
                "vm",
                "extension",
                "set",
                "--resource-group",
                "STS2",
                "--vm-name",
                "sts2_vm",
                "--publisher",
                "Microsoft.Compute",
                "--name",
                "CustomScriptExtension",
                "--settings",
                f"@{settings_path}",
                "-o",
                "json",
            ],
            check=True,
        )
    finally:
        try:
            os.remove(settings_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
