"""Sensor entities for the Govee Life integration."""

from __future__ import annotations
from typing import Final
import logging
import asyncio

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.const import (
    CONF_DEVICES,
    STATE_UNKNOWN,
    UnitOfTemperature,
)

from .entities import GoveeLifePlatformEntity
from .const import DOMAIN, CONF_COORDINATORS
from .utils import GoveeAPI_GetCachedStateValue, async_GoveeAPI_ControlDevice

_LOGGER: Final = logging.getLogger(__name__)
PLATFORM = 'climate'
PLATFORM_DEVICE_TYPES = [
    'devices.types.heater',
    'devices.types.kettle',
]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the climate platform."""
    _LOGGER.debug("Setting up %s platform entry: %s | %s", PLATFORM, DOMAIN, entry.entry_id)
    entities = []

    try:
        entry_data = hass.data[DOMAIN][entry.entry_id]
        api_devices = entry_data[CONF_DEVICES]
    except Exception as e:
        _LOGGER.error("%s - async_setup_entry %s: Failed to get cloud devices from data store: %s (%s.%s)", entry.entry_id, PLATFORM, str(e), e.__class__.__module__, type(e).__name__)
        return

    for device_cfg in api_devices:
        try:
            if device_cfg.get('type') not in PLATFORM_DEVICE_TYPES:
                continue
            device = device_cfg.get('device')
            coordinator = entry_data[CONF_COORDINATORS][device]
            entity = GoveeLifeClimate(hass, entry, coordinator, device_cfg, platform=PLATFORM)
            entities.append(entity)
            await asyncio.sleep(0)
        except Exception as e:
            _LOGGER.error("%s - async_setup_entry %s: Failed to setup device: %s (%s.%s)", entry.entry_id, PLATFORM, str(e), e.__class__.__module__, type(e).__name__)
            return

    if entities:
        async_add_entities(entities)


class GoveeLifeClimate(ClimateEntity, GoveeLifePlatformEntity):
    """Climate class for Govee Life integration."""

    _attr_hvac_modes = []
    _attr_hvac_modes_mapping = {}
    _attr_hvac_modes_mapping_set = {}
    _attr_preset_modes = []
    _attr_preset_modes_mapping = {}
    _attr_preset_modes_mapping_set = {}
    _enable_turn_on_off_backwards_compatibility = False

    def _init_platform_specific(self, **kwargs):
        """Platform specific init actions."""
        _LOGGER.debug("%s - %s: _init_platform_specific", self._api_id, self._identifier)
        capabilities = self._device_cfg.get('capabilities', [])

        _LOGGER.debug("%s - %s: _init_platform_specific: processing devices request capabilities", self._api_id, self._identifier)
        for cap in capabilities:
            if cap['type'] == 'devices.capabilities.on_off':
                for option in cap['parameters']['options']:
                    if option['name'] == 'on':
                        self._attr_supported_features |= ClimateEntityFeature.TURN_ON
                        self._attr_hvac_modes.append(HVACMode.HEAT_COOL)
                        self._attr_hvac_modes_mapping[option['value']] = HVACMode.HEAT_COOL
                        self._attr_hvac_modes_mapping_set[HVACMode.HEAT_COOL] = option['value']
                    elif option['name'] == 'off':
                        self._attr_supported_features |= ClimateEntityFeature.TURN_OFF
                        self._attr_hvac_modes.append(HVACMode.OFF)
                        self._attr_hvac_modes_mapping[option['value']] = HVACMode.OFF
                        self._attr_hvac_modes_mapping_set[HVACMode.OFF] = option['value']
                    else:
                        _LOGGER.warning("%s - %s: _init_platform_specific: unknown on_off option: %s", self._api_id, self._identifier, option)
            elif cap['type'] == 'devices.capabilities.temperature_setting' and (cap['instance'] in ['targetTemperature', 'sliderTemperature']):
                self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
                for field in cap['parameters']['fields']:
                    if field['fieldName'] == 'temperature':
                        self._attr_max_temp = field['range']['max']
                        self._attr_min_temp = field['range']['min']
                        self._attr_target_temperature_step = field['range']['precision']
                    elif field['fieldName'] == 'unit':
                        self._attr_temperature_unit = UnitOfTemperature[field['defaultValue'].upper()]
                    elif field['fieldName'] == 'autoStop':
                        pass  # TO-BE-DONE: implement as switch entity type
            elif cap['type'] == 'devices.capabilities.work_mode':
                self._attr_supported_features |= ClimateEntityFeature.PRESET_MODE
                self._attr_preset_modes = []
                self._attr_preset_modes_mapping = {}
                self._attr_preset_modes_mapping_set = {}

                work_field = next((f for f in cap['parameters']['fields'] if f['fieldName'] == 'workMode'), None)
                mode_field = next((f for f in cap['parameters']['fields'] if f['fieldName'] == 'modeValue'), None)

                if not work_field or not mode_field:
                    _LOGGER.warning("%s - %s: missing workMode or modeValue fields", self._api_id, self._identifier)
                else:
                    for work_option in work_field.get('options', []):
                        name = work_option['name']
                        value = work_option['value']

                        # Handle sub-options for gearMode (Low/Medium/High)
                        if name == "gearMode":
                            for sub in mode_field.get('options', []):
                                if sub.get('name') != "gearMode":
                                    continue
                                for level in sub.get('options', []):
                                    level_name = level['name']
                                    level_value = level['value']
                                    mode_name = f"{name}-{level_name}"  # e.g. "gearMode-Low"
                                    self._attr_preset_modes.append(mode_name)
                                    self._attr_preset_modes_mapping[mode_name] = value
                                    self._attr_preset_modes_mapping_set[mode_name] = {
                                        "workMode": value,
                                        "modeValue": level_value,
                                    }
                        else:
                            # Fan / Auto modes
                            default_val = 0
                            for f in mode_field.get('options', []):
                                if f.get('name') == name:
                                    default_val = f.get('defaultValue', 0)
                            self._attr_preset_modes.append(name)
                            self._attr_preset_modes_mapping[name] = value
                            self._attr_preset_modes_mapping_set[name] = {
                                "workMode": value,
                                "modeValue": default_val,
                            }

                _LOGGER.debug("%s - %s: Available preset modes: %s", self._api_id, self._identifier, self._attr_preset_modes)
            elif cap['type'] == 'devices.capabilities.property' and cap['instance'] == 'sensorTemperature':
                pass  # do nothing as this is handled within 'current_temperature' property
            else:
                _LOGGER.debug("%s - %s: _init_platform_specific: cap unhandled: %s", self._api_id, self._identifier, cap)

    @property
    def hvac_mode(self) -> str:
        """Return the hvac_mode of the entity."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.on_off', 'powerSwitch')
        v = self._attr_hvac_modes_mapping.get(value, STATE_UNKNOWN)
        if v == STATE_UNKNOWN:
            _LOGGER.warning("%s - %s: hvac_mode: invalid value: %s", self._api_id, self._identifier, value)
        return v

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        state_capability = {
            "type": "devices.capabilities.on_off",
            "instance": "powerSwitch",
            "value": self._attr_hvac_modes_mapping_set[hvac_mode]
        }
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
            self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn the entity off."""
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        """Turn the entity on."""
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    @property
    def preset_mode(self) -> str | None:
        """Return the preset_mode of the entity."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.work_mode', 'workMode')
        if not value:
            return None
        work_mode = value.get("workMode")
        mode_value = value.get("modeValue", 0)

        # Find the preset mode name that matches this workMode and modeValue
        for preset_name, preset in self._attr_preset_modes_mapping_set.items():
            if preset["workMode"] == work_mode and preset["modeValue"] == mode_value:
                return preset_name

        return None

    async def async_set_preset_mode(self, preset_mode) -> None:
        """Set new target preset mode."""
        preset_value = self._attr_preset_modes_mapping_set.get(preset_mode)
        if not preset_value:
            _LOGGER.warning("%s - %s: Unknown preset mode requested: %s", self._api_id, self._identifier, preset_mode)
            return

        state_capability = {
            "type": "devices.capabilities.work_mode",
            "instance": "workMode",
            "value": preset_value
        }
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
            self.async_write_ha_state()

    @property
    def temperature_unit(self) -> str:
        """Return the temperature unit of the entity."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.temperature_setting', 'targetTemperature')
        if value is not None:
            return UnitOfTemperature[value.get("unit", "CELSIUS").upper()]

        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.temperature_setting', 'sliderTemperature')
        if value is not None:
            return UnitOfTemperature[value.get("unit", "CELSIUS").upper()]

        return UnitOfTemperature.CELSIUS

    @property
    def target_temperature(self) -> float | None:
        """Return the target temperature of the entity."""
        preset_mode = self.preset_mode
        _LOGGER.debug("%s - %s: target_temperature: current preset mode: %s", self._api_id, self._identifier, preset_mode)

        if preset_mode and preset_mode in self._attr_preset_modes_mapping_set:
            mode_value = self._attr_preset_modes_mapping_set[preset_mode].get("modeValue")
            if mode_value is not None and mode_value != 0:
                return float(mode_value)

        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.temperature_setting', 'sliderTemperature')
        if value is None:
            return None
        temperature = value.get("targetTemperature")
        if temperature is None:
            return None
        return float(temperature)

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""        
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.temperature_setting', 'targetTemperature')
        unit = value.get('unit', 'Celsius')
        state_capability = {
            "type": "devices.capabilities.temperature_setting",
            "instance": "targetTemperature",
            "value": {
                "temperature": kwargs['temperature'],
                "unit": unit,
            }
        }
        if await async_GoveeAPI_ControlDevice(self.hass, self._entry_id, self._device_cfg, state_capability):
            self.async_write_ha_state()

    @property
    def current_temperature(self) -> float | None:
        """Return the current temperature of the entity."""
        value = GoveeAPI_GetCachedStateValue(self.hass, self._entry_id, self._device_cfg.get('device'), 'devices.capabilities.property', 'sensorTemperature')
        if value is None or value == "":
            return None
        if self.temperature_unit == UnitOfTemperature.CELSIUS:
            value = (float(value) - 32) * 5 / 9
        return float(value)
