import asyncio
import re
import shutil
import threading
import time

from typer.testing import CliRunner

from eivlhole import paths, transfer
from eivlhole.cli.app import app
from eivlhole.mailbox.sftp import SftpMailbox
from eivlhole.ssh.client import sftp

runner = CliRunner()
CODE_RE = re.compile(r"\b\d{1,3}(?:-[a-z]+){3}\b")


def setup(config_dir, ssh_server, client_key):
    (ssh_server.root / "drop" / "codes").mkdir(parents=True)
    (ssh_server.root / "drop" / "transfers").mkdir()
    paths.config_file().write_text(
        "[defaults]\n"
        'host = "drop"\n'
        'drop_root = "/drop"\n'
        "code_words = 3\n\n"
        "[hosts.drop]\n"
        f'hostname = "{ssh_server.host}"\n'
        f"port = {ssh_server.port}\n"
        f'user = "{ssh_server.user}"\n'
        f'key = "{client_key.as_posix()}"\n'
    )
    shutil.copy(ssh_server.known_hosts, paths.known_hosts_file())


def test_send_and_receive_via_cli(config_dir, ssh_server, client_key, target, tmp_path):
    setup(config_dir, ssh_server, client_key)
    src = tmp_path / "report.txt"
    src.write_text("quarterly")
    r = runner.invoke(app, ["send", "--no-wait", str(src)])
    assert r.exit_code == 0, r.output
    code = CODE_RE.search(r.output).group(0)
    assert "eivlhole receive" in r.output
    out = tmp_path / "inbox"
    r = runner.invoke(app, ["receive", code, "--out", str(out), "--yes"])
    assert r.exit_code == 0, r.output
    assert (out / "report.txt").read_text() == "quarterly"
    assert "report.txt" in r.output


def test_receive_prompts_and_can_decline(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key)
    src = tmp_path / "a.txt"
    src.write_text("a")
    code = CODE_RE.search(runner.invoke(app, ["send", "--no-wait", str(src)]).output).group(0)
    r = runner.invoke(app, ["receive", "--out", str(tmp_path / "o")], input=f"{code}\nn\n")
    assert r.exit_code == 1 and "cancelled" in r.output
    r = runner.invoke(app, ["receive", "--out", str(tmp_path / "o")], input=f"{code}\ny\n")
    assert r.exit_code == 0 and (tmp_path / "o" / "a.txt").exists()


def test_receive_unknown_code(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key)
    r = runner.invoke(app, ["receive", "9-nothing-here-now", "--yes", "--out", str(tmp_path)])
    assert r.exit_code == 1 and "not found" in r.output


def test_receive_unattended_requires_code(config_dir, ssh_server, client_key):
    setup(config_dir, ssh_server, client_key)
    r = runner.invoke(app, ["--unattended", "receive"])
    assert r.exit_code == 1 and "code is required" in r.output


def test_send_missing_file(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key)
    r = runner.invoke(app, ["send", "--no-wait", str(tmp_path / "ghost")])
    assert r.exit_code == 1 and "no such file" in r.output


def test_send_waits_for_receiver(config_dir, ssh_server, client_key, target, tmp_path):
    setup(config_dir, ssh_server, client_key)
    src = tmp_path / "w.txt"
    src.write_text("w")
    captured = {}

    def receiver():
        async def go():
            codes = ssh_server.root / "drop" / "codes"
            deadline = time.monotonic() + 10
            while len(list(codes.iterdir())) != 1:
                if time.monotonic() > deadline:
                    raise TimeoutError("no code appeared")
                await asyncio.sleep(0.05)
            code = next(codes.iterdir()).name
            async with sftp(target, known_hosts=ssh_server.known_hosts) as s:
                mb = SftpMailbox(s, "/drop")
                captured["result"] = await transfer.receive(
                    code, mb, tmp_path / "o", history=None, host_name="drop"
                )

        try:
            asyncio.run(go())
        except Exception as exc:
            captured["error"] = exc

    t = threading.Thread(target=receiver)
    t.start()
    r = runner.invoke(app, ["send", "--poll", "0.1", str(src)])
    t.join(15)
    assert not t.is_alive()
    assert "error" not in captured, captured.get("error")
    assert r.exit_code == 0, r.output
    assert "received" in r.output
    assert re.fullmatch(r"[0-9a-f]{32}", captured["result"].transfer_id)
    assert (tmp_path / "o" / "w.txt").read_text() == "w"


def test_send_to_unknown_host(config_dir, ssh_server, client_key, tmp_path):
    setup(config_dir, ssh_server, client_key)
    src = tmp_path / "a.txt"
    src.write_text("a")
    r = runner.invoke(app, ["send", "--to", "nope", str(src)])
    assert r.exit_code == 1 and "unknown host 'nope'" in r.output
