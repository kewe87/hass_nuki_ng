from __future__ import annotations

import logging
import secrets

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .constants import (
    AUTH_AUTO,
    AUTH_MODES,
    CONF_ACTION_RETRIES,
    CONF_AUTH_MODE,
    CONF_WEBHOOK_ID,
    DEFAULT_ACTION_RETRIES,
    DOMAIN,
)
from .nuki import NukiAuthError, NukiError, NukiInterface, redact

_LOGGER = logging.getLogger(__name__)

PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
TEXT = TextSelector()
URL = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))


def _seconds(minimum, maximum):
    return NumberSelector(
        NumberSelectorConfig(min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX, unit_of_measurement="s")
    )


async def _validate(hass, config: dict) -> tuple[str | None, str | None, str | None]:
    """Check the credentials. Returns (title, unique_id, error)."""
    nuki = NukiInterface(
        hass,
        bridge=config.get("address"),
        token=config.get("token"),
        web_token=config.get("web_token"),
        auth_mode=config.get(CONF_AUTH_MODE, AUTH_AUTO),
    )
    title = None
    unique_id = None
    if nuki.token:
        try:
            info = await nuki.resolve_auth()
            unique_id = info.get("ids", {}).get("hardwareId")
            devices = await nuki.bridge_list()
        except NukiAuthError:
            return None, None, "invalid_bridge_token"
        except NukiError as err:
            _LOGGER.warning("Bridge check failed: %s", redact(err))
            return None, None, "cannot_connect"
        title = next(iter(devices.values()), {}).get("name")
        if not title:
            return None, None, "no_devices"
    if nuki.web_token:
        try:
            devices = await nuki.web_list()
        except NukiError as err:
            _LOGGER.warning("Web API check failed: %s", redact(err))
            return None, None, "invalid_web_token"
        title = title or next(iter(devices.values()), {}).get("name")
    return title, str(unique_id) if unique_id is not None else None, None


class NukiNGConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 1

    def _hass_url(self) -> str:
        try:
            url = get_url(self.hass, allow_external=False, allow_cloud=False, allow_ip=True)
        except NoURLAvailableError:
            return ""
        return url if url.lower().startswith("http://") else ""

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is None:
            user_input = {
                "address": await NukiInterface(self.hass).discover_bridge(),
                "hass_url": self._hass_url(),
            }
        else:
            hass_url = (user_input.get("hass_url") or "").strip().rstrip("/")
            if not user_input.get("token") and not user_input.get("web_token"):
                errors["base"] = "no_token"
            elif user_input.get("token") and not user_input.get("address"):
                errors["base"] = "no_bridge_url"
            elif hass_url.lower().startswith("https://"):
                errors["base"] = "https_not_supported"
            else:
                title, unique_id, err = await _validate(self.hass, user_input)
                if err:
                    errors["base"] = err
                else:
                    if unique_id:
                        await self.async_set_unique_id(unique_id)
                        self._abort_if_unique_id_configured()
                    data = {
                        "address": user_input.get("address", ""),
                        "token": user_input.get("token", ""),
                        "web_token": user_input.get("web_token", ""),
                        "name": user_input.get("name", ""),
                        CONF_WEBHOOK_ID: secrets.token_hex(32),
                    }
                    options = {
                        "hass_url": hass_url,
                        "update_seconds": int(user_input.get("update_seconds", 30)),
                        CONF_AUTH_MODE: AUTH_AUTO,
                        CONF_ACTION_RETRIES: DEFAULT_ACTION_RETRIES,
                    }
                    return self.async_create_entry(
                        title=user_input.get("name") or title or "Nuki", data=data, options=options
                    )
        schema = vol.Schema({
            vol.Optional("address", default=user_input.get("address", "")): TEXT,
            vol.Optional("token"): PASSWORD,
            vol.Optional("web_token"): PASSWORD,
            vol.Optional("hass_url", default=user_input.get("hass_url", "")): URL,
            vol.Optional("name", default=user_input.get("name", "")): TEXT,
            vol.Required("update_seconds", default=user_input.get("update_seconds", 30)): _seconds(10, 600),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            config = {**entry.data, **entry.options}
            if user_input.get("token"):
                config["token"] = user_input["token"]
            if user_input.get("web_token"):
                config["web_token"] = user_input["web_token"]
            _, unique_id, err = await _validate(self.hass, config)
            if err:
                errors["base"] = err
            elif entry.unique_id and unique_id and unique_id != entry.unique_id:
                errors["base"] = "wrong_bridge"
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        "token": config.get("token", ""),
                        "web_token": config.get("web_token", ""),
                    },
                )
        schema = vol.Schema({
            vol.Optional("token"): PASSWORD,
            vol.Optional("web_token"): PASSWORD,
        })
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={"name": entry.title},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):

    async def async_step_init(self, user_input=None):
        current = {**self.config_entry.data, **self.config_entry.options}
        errors = {}
        if user_input is not None:
            # An emptied optional field is not submitted at all: treat it as
            # "detect automatically" instead of keeping the old value.
            hass_url = (user_input.get("hass_url") or "").strip().rstrip("/")
            if hass_url.lower().startswith("https://"):
                errors["base"] = "https_not_supported"
            else:
                return self.async_create_entry(
                    data={
                        "hass_url": hass_url,
                        "update_seconds": int(user_input["update_seconds"]),
                        CONF_ACTION_RETRIES: int(user_input[CONF_ACTION_RETRIES]),
                        CONF_AUTH_MODE: user_input[CONF_AUTH_MODE],
                    }
                )
        schema = vol.Schema({
            vol.Optional(
                "hass_url",
                description={"suggested_value": current.get("hass_url", "")},
            ): URL,
            vol.Required("update_seconds", default=int(current.get("update_seconds", 30))): _seconds(10, 600),
            vol.Required(
                CONF_ACTION_RETRIES,
                default=int(current.get(CONF_ACTION_RETRIES, DEFAULT_ACTION_RETRIES)),
            ): NumberSelector(NumberSelectorConfig(min=0, max=5, step=1, mode=NumberSelectorMode.BOX)),
            vol.Required(CONF_AUTH_MODE, default=current.get(CONF_AUTH_MODE, AUTH_AUTO)): SelectSelector(
                SelectSelectorConfig(
                    options=AUTH_MODES,
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key=CONF_AUTH_MODE,
                )
            ),
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
