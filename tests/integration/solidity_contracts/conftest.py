import logging

import pytest
from starkware.starknet.core.os.contract_address.contract_address import (
    calculate_contract_address_from_hash,
)
from starkware.starknet.testing.contract import StarknetContract
from web3 import Web3

from tests.utils.contracts import get_contract, use_kakarot_backend
from tests.utils.helpers import hex_string_to_bytes_array
from tests.utils.reporting import traceit

logger = logging.getLogger()


@pytest.fixture(scope="package")
def get_starknet_address(account_proxy_class, kakarot):
    """
    Fixture to return the starknet address of a contract deployed by kakarot using CREATE2
    """

    def _factory(evm_contract_address):
        return calculate_contract_address_from_hash(
            salt=evm_contract_address,
            class_hash=account_proxy_class.class_hash,
            constructor_calldata=[],
            deployer_address=kakarot.contract_address,
        )

    return _factory


@pytest.fixture(scope="package")
def get_solidity_contract(starknet, contract_account_class, kakarot):
    """
    Fixture to attach a modified web3.contract instance to an already deployed contract_account in kakarot.
    """

    def _factory(
        contract_app, contract_name, starknet_contract_address, evm_contract_address, tx
    ):
        """
        This factory is what is actually returned by pytest when requesting the `get_solidity_contract`
        fixture.
        It creates a web3.contract based on the basename of the target solidity file.
        """
        contract_account = StarknetContract(
            starknet.state,
            contract_account_class.abi,
            starknet_contract_address,
            tx,
        )
        contract = get_contract(contract_app, contract_name)
        kakarot_contract = use_kakarot_backend(
            contract, kakarot, int(evm_contract_address, 16)
        )
        setattr(kakarot_contract, "contract_account", contract_account)
        setattr(kakarot_contract, "evm_contract_address", evm_contract_address)

        return kakarot_contract

    return _factory


@pytest.fixture(scope="package")
def deploy_solidity_contract(kakarot, get_solidity_contract):
    """
    Fixture to deploy a solidity contract in kakarot. The returned contract is a modified
    web3.contract instance with an added `contract_account` attribute that return the actual
    underlying kakarot contract account.
    """

    async def _factory(contract_app, contract_name, *args, **kwargs):
        """
        This factory is what is actually returned by pytest when requesting the `deploy_solidity_contract`
        fixture.
        It creates a web3.contract based on the basename of the target solidity file.
        This contract is deployed to kakarot using the deploy bytecode generated by web3.contract.
        Eventually, the web3.contract is updated such that each function (view or write) targets instead kakarot.

        The args and kwargs are passed as is to the web3.contract.constructor. Only the `caller_eoa` kwarg is
        is required and filtered out before calling the constructor.
        """
        contract = get_contract(contract_app, contract_name)
        if "caller_eoa" not in kwargs:
            raise ValueError(
                "caller_eoa needs to be given in kwargs for deploying the contract"
            )
        caller_eoa = kwargs["caller_eoa"]
        del kwargs["caller_eoa"]
        deploy_bytecode = hex_string_to_bytes_array(
            contract.constructor(*args, **kwargs).data_in_transaction
        )
        with traceit.context(contract_name):
            await caller_eoa.starknet_contract.increment_nonce().execute()
            tx = await kakarot.eth_send_transaction(
                to=0, gas_limit=1_000_000, gas_price=0, value=0, data=deploy_bytecode
            ).execute(caller_address=caller_eoa.starknet_address)

        deploy_event = [
            e
            for e in tx.main_call_events
            if type(e).__name__ == "evm_contract_deployed"
        ][0]
        starknet_contract_address = deploy_event.starknet_contract_address
        evm_contract_address = Web3.toChecksumAddress(
            f"{deploy_event.evm_contract_address:040x}"
        )
        return get_solidity_contract(
            contract_app,
            contract_name,
            starknet_contract_address,
            evm_contract_address,
            tx,
        )

    return _factory