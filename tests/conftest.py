# SPDX-License-Identifier: AGPL-3.0-only
import pytest

from printhub.config import Config

# argon2 hash of the password "secret"
PASSWORD_HASH = "$argon2id$v=19$m=102400,t=2,p=8$cVi6o9hcO50iJ4TXGtsXtw$KCpY1FyyOdUWb2MQ9PwEvQ"


@pytest.fixture
def cfg(tmp_path) -> Config:
    return Config(
        password_hash=PASSWORD_HASH,
        session_secret="test-session-secret",
        smtp_host="127.0.0.1",
        smtp_port=8025,
        smtp_security="starttls",
        smtp_user="printhub",
        smtp_password="smtp-secret",
        mail_from="printhub@example.com",
        data_dir=tmp_path / "data",
    )
