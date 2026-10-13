"""linked_terms.toml is frozen (Physician sign-off). Any change needs fresh sign-off and a new checksum commit."""
import hashlib, pathlib

HERE = pathlib.Path(__file__).resolve().parent


def test_linked_terms_checksum_unchanged():
    want = (HERE / "LINKED_TERMS.SHA256").read_text().split()
    assert want[1] == "linked_terms.toml"
    assert hashlib.sha256((HERE / "linked_terms.toml").read_bytes()).hexdigest() == want[0]
