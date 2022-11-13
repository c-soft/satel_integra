#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Tests for encryption module."""

import pytest
from unittest.mock import patch, MagicMock
from satel_integra2.encryption import EncryptedCommunicationHandler, SatelEncryption


class TestProtocol:

    """Verify Satel PDU."""

    def test_prepare_pdu(self):
        """Verify protocol data unit preparation."""
        communication_handler = EncryptedCommunicationHandler('some_key')
        with patch.object(communication_handler, '_prepare_header', MagicMock(return_value=b'some_header')):
            with patch.object(SatelEncryption, 'encrypt', MagicMock(side_effect=lambda x: x)) as encrypt_mock:
                pdu = communication_handler.prepare_pdu(b'some_message')
                assert pdu == b'some_headersome_message'
                encrypt_mock.assert_called_with(b'some_headersome_message')

    def test_extract_data_from_pdu(self):
        """Verify data extraction from PDU."""
        EncryptedCommunicationHandler.next_id_s = ord(b'r')
        communication_handler = EncryptedCommunicationHandler('some_key')
        with patch.object(SatelEncryption, 'decrypt', side_effect=lambda x: x) as decrypt_mock:
            data = communication_handler.extract_data_from_pdu(b'headersome_message')
            assert data == b'some_message'
            decrypt_mock.assert_called_with(b'headersome_message')

    def test_rolling_counter(self):
        """Verify rolling counter.

        Rolling counter shall be included in PDU header and incremented by one for each constructed PDU.

        """
        with patch.object(SatelEncryption, 'encrypt', MagicMock(side_effect=lambda x: x)):
            communication_handler = EncryptedCommunicationHandler('some_key')
            assert communication_handler.prepare_pdu(b'')[2:4] == b'\x00\x00'
            assert communication_handler.prepare_pdu(b'')[2:4] == b'\x00\x01'
            communication_handler._rolling_counter = 0x00FF
            assert communication_handler.prepare_pdu(b'')[2:4] == b'\x00\xFF'
            assert communication_handler.prepare_pdu(b'')[2:4] == b'\x01\x00'
            communication_handler._rolling_counter = 0xFFFF
            assert communication_handler.prepare_pdu(b'')[2:4] == b'\xFF\xFF'
            assert communication_handler.prepare_pdu(b'')[2:4] == b'\x00\x00'

    def test_id_s(self):
        """Test verification of received ID_S.

        Received ID_S shall be the same as last sent.

        """
        with patch.object(SatelEncryption, 'encrypt', MagicMock(side_effect=lambda x: x)):
            communication_handler = EncryptedCommunicationHandler('some_key')
            current_id_s = communication_handler.prepare_pdu(b'')[4]
            with patch.object(SatelEncryption, 'decrypt', side_effect=lambda x: x):
                communication_handler.extract_data_from_pdu(b'heade%bsome_message' % current_id_s.to_bytes(1, 'big'))
                with pytest.raises(RuntimeError):
                    communication_handler.extract_data_from_pdu(
                        b'heade%bsome_message' % (current_id_s + 1).to_bytes(1, 'big'))


class TestEncryption:

    """Verify encryption and decryption."""

    def test_key_conversion(self):
        """Verify conversion of integration key to encryption key."""
        assert SatelEncryption.integration_key_to_encryption_key('') == b'                        '
        assert SatelEncryption.integration_key_to_encryption_key('short_key') == b'short_key   short_key   '
        assert SatelEncryption.integration_key_to_encryption_key('long_key1234') == b'long_key1234long_key1234'
        assert SatelEncryption.integration_key_to_encryption_key('too_long_key1234') == b'too_long_keytoo_long_key'

    def test_encrypt(self):
        """Verify encryption."""
        encryption_handler = SatelEncryption('some_key')
        message = b'\x52\x3D\x00\x03\x00\x54\xFE\xFE\x7F\xFF\xFF\xFF\xFF\xFF\xFF\xFE\xF0\x8A\xFE\x0D'
        encrypted = encryption_handler.encrypt(message)
        assert encrypted == b'\x43\xD8\x64\x30\x7E\x6B\x84\x31\xCD\x75\x59\xCE\x2E\xF9\xD4\x1F\x26\x61\x5C\x2F'

    def test_decrypt(self):
        """Verify decryption."""
        encryption_handler = SatelEncryption('some_key')
        message = b'\x43\xD8\x64\x30\x7E\x6B\x84\x31\xCD\x75\x59\xCE\x2E\xF9\xD4\x1F\x26\x61\x5C\x2F'
        decrypted = encryption_handler.decrypt(message)
        assert decrypted == b'\x52\x3D\x00\x03\x00\x54\xFE\xFE\x7F\xFF\xFF\xFF\xFF\xFF\xFF\xFE\xF0\x8A\xFE\x0D'

    def test_short_message(self):
        """Verify encryption for message shorter than encryption block.

        Padding shall be added in this case.

        """
        encryption_handler = SatelEncryption('some_key')
        message = b'short message'
        encrypted = encryption_handler.encrypt(message)
        assert encrypted != message
        decrypted = encryption_handler.decrypt(encrypted)
        assert decrypted == message + b'\x00\x00\x00'
