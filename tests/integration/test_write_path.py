"""Інтеграційні тести шляху запису.

Виконуються зі справжніми сховищами: перевіряють розподіл між
вузлами, пакетний запис і синхронність трьох сховищ.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skip(reason="потребує піднятих сховищ")
async def test_batch_write_reaches_all_stores() -> None:
    """Документ після запису доступний у сховищі, індексі та графі."""
    raise NotImplementedError


@pytest.mark.skip(reason="потребує піднятих сховищ")
async def test_shard_distribution_is_even() -> None:
    """Розподіл між трьома вузлами відхиляється не більш ніж на 5%."""
    raise NotImplementedError


@pytest.mark.skip(reason="потребує піднятих сховищ")
async def test_invalid_document_is_rejected() -> None:
    """Негативний сценарій: документ без обов'язкових полів не зберігається."""
    raise NotImplementedError
