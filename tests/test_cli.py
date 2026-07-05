import argparse
import base64

from cli import cmd_generate_share_qr


def test_generate_share_qr_command_writes_output(tmp_path, monkeypatch):
    class DummyShares:
        def __init__(self):
            self.calls = []

        def generate_qr_code(self, share_id):
            self.calls.append(share_id)
            return base64.b64encode(b"fake-png").decode("ascii")

    class DummyVault:
        pass

    dummy_shares = DummyShares()
    monkeypatch.setattr("cli._get_vault", lambda: DummyVault())
    monkeypatch.setattr("cli._unlock_vault", lambda vault, passphrase=None: None)
    monkeypatch.setattr("cli._get_shares", lambda vault: dummy_shares)
    monkeypatch.setattr("cli._open_file", lambda path: None)

    output_path = tmp_path / "share.png"
    cmd_generate_share_qr(argparse.Namespace(passphrase=None, share_id="abc123", output=str(output_path)))

    assert dummy_shares.calls == ["abc123"]
    assert output_path.read_bytes() == b"fake-png"
