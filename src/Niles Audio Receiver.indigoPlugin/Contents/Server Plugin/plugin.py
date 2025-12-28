#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""
Niles Audio Receiver Plugin for Indigo
Developed by RogueProeliator <rp@rogueproeliator.com>

This plugin allows Indigo to control Niles Audio multi-zone receivers such as
the ZR-4 and ZR-6, as well as some compatible Monoprice models.

Rewritten for Indigo 2025.1 without RPFramework dependency.

Command structure based on Niles Audio's published specification found in the ZR-6
instruction manual.
"""

# region Python Imports
import logging
import time
from typing import Dict, List, Optional, Tuple

import indigo

from niles_receiver import NilesReceiver, MAX_VOLUME, BRIGHTNESS_TO_VOLUME_FACTOR
from niles_zone import NilesZone
# endregion

# region Constants
LOG_FORMAT = '%(asctime)s.%(msecs)03d\t%(levelname)-10s\t%(name)s.%(funcName)-28s %(message)s'

# Debug level mapping from plugin prefs to Python logging levels
DEBUG_LEVEL_MAP = {
    "0": logging.WARNING,  # Off = minimal logging
    "1": logging.INFO,     # Low = info level
    "2": logging.DEBUG     # High = debug level
}
# endregion


class Plugin(indigo.PluginBase):
    """
    Main plugin class for Niles Audio Receiver.
    
    This plugin controls Niles Audio zone receivers via serial connection,
    allowing control of power, volume, source, mute, and tuner functions.
    """

    # ========================================================================
    # region Class Construction and Destruction
    # ========================================================================
    def __init__(self, plugin_id: str, plugin_display_name: str,
                 plugin_version: str, plugin_prefs: indigo.Dict):
        """
        Initialize the plugin.
        
        Args:
            plugin_id: The unique identifier for this plugin
            plugin_display_name: Human-readable plugin name
            plugin_version: Plugin version string
            plugin_prefs: Saved plugin preferences
        """
        super().__init__(plugin_id, plugin_display_name, plugin_version, plugin_prefs)
        
        # Initialization flags
        self.plugin_is_initializing = True
        self.plugin_is_shutting_down = False
        
        # Configure logging
        debug_level_str = self.pluginPrefs.get('debugLevel', '0')
        self.debug_level = DEBUG_LEVEL_MAP.get(debug_level_str, logging.WARNING)
        
        self.plugin_file_handler.setFormatter(
            logging.Formatter(fmt=LOG_FORMAT, datefmt='%Y-%m-%d %H:%M:%S')
        )
        self.indigo_log_handler.setLevel(self.debug_level)
        
        # Device tracking
        # Receivers: maps device ID to NilesReceiver instance
        self.managed_receivers: Dict[int, NilesReceiver] = {}
        # Zones: maps device ID to NilesZone instance
        self.managed_zones: Dict[int, NilesZone] = {}
        
        self.logger.debug("Plugin __init__ complete")
        self.plugin_is_initializing = False

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Plugin Lifecycle Methods
    # ========================================================================
    def startup(self) -> None:
        """
        Called after plugin initialization.
        """
        self.logger.info("Plugin starting...")
        
        # Initialize all receiver devices to a known state
        for dev in indigo.devices.iter("self"):
            if dev.deviceTypeId == 'nilesAudioReceiver':
                self.logger.debug(f"Initializing receiver device: {dev.name}")
                dev.updateStateOnServer('connectionState', value='Starting')
        
        self.logger.info("Plugin started successfully")

    def upgrade_zone_devices_to_dimmer(self, values_dict: indigo.Dict = None,
                                        type_id: str = "") -> None:
        """
        Menu callback to upgrade legacy zone devices to dimmer type.
        
        This method converts legacy custom zone devices (nilesAudioZone) to the 
        new dimmer-based zone type (nilesAudioZoneDimmer), which allows native 
        volume control in Indigo clients.
        
        Args:
            values_dict: Menu dialog values (unused)
            type_id: Menu type identifier (unused)
        """
        upgrade_count = 0
        
        for dev in indigo.devices.iter("self"):
            # Convert legacy nilesAudioZone (custom) to nilesAudioZoneDimmer (dimmer)
            if dev.deviceTypeId == 'nilesAudioZone':
                try:
                    self.logger.info(f"Upgrading zone device '{dev.name}' from legacy custom type to dimmer type")
                    
                    # Change the device type - this preserves all properties and states
                    new_dev = indigo.device.changeDeviceTypeId(dev, 'nilesAudioZoneDimmer')
                    
                    # The old device reference is now stale, use new_dev going forward
                    # Force a state list refresh to pick up the dimmer states
                    new_dev.stateListOrDisplayStateIdChanged()
                    
                    upgrade_count += 1
                    self.logger.info(f"Successfully upgraded '{new_dev.name}' to dimmer type")
                    
                except Exception as e:
                    self.logger.error(f"Failed to upgrade zone device '{dev.name}': {e}")
        
        if upgrade_count > 0:
            self.logger.info(f"Upgraded {upgrade_count} zone device(s) to dimmer type")

    def shutdown(self) -> None:
        """
        Called when plugin is shutting down.
        """
        self.logger.info("Plugin shutting down...")
        self.plugin_is_shutting_down = True
        
        # Stop all receiver threads
        for dev_id, receiver in self.managed_receivers.items():
            try:
                receiver.stop()
            except Exception as e:
                self.logger.warning(f"Error stopping receiver {dev_id}: {e}")
        
        self.logger.info("Plugin shutdown complete")

    def runConcurrentThread(self) -> None:
        """
        Main plugin loop for status polling.
        """
        self.logger.debug("Concurrent thread starting")
        self.sleep(1)  # Initial pause
        
        try:
            loop_count = 0
            while True:
                loop_count += 1
                
                # Log status periodically
                if loop_count % 30 == 1:  # Every ~60 seconds
                    self.logger.debug(f"Concurrent thread running - {len(self.managed_receivers)} receivers")
                
                # Check each managed receiver for status update
                for dev_id, receiver in list(self.managed_receivers.items()):
                    try:
                        dev = indigo.devices.get(dev_id)
                        if dev and self._time_to_poll(dev, receiver):
                            self.logger.debug(f"Polling status for: {dev.name}")
                            receiver.poll_all_zones()
                    except Exception as e:
                        self.logger.error(f"Error checking receiver {dev_id}: {e}")
                
                self.sleep(2)  # Main loop interval
                
        except self.StopThread:
            self.logger.info("Concurrent thread stopping")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Device Communication Methods
    # ========================================================================
    def deviceStartComm(self, dev: indigo.Device) -> None:
        """
        Called when device communication should start.
        
        Args:
            dev: The Indigo device to start communication with
        """
        self.logger.info(f"Starting communication with {dev.name}")
        
        try:
            if dev.deviceTypeId == 'nilesAudioReceiver':
                # Update state to indicate we're starting
                dev.updateStateOnServer('connectionState', value='Starting')
                
                # Create receiver device manager instance
                receiver = NilesReceiver(self, dev)
                self.managed_receivers[dev.id] = receiver
                receiver.start()
                
                # Register any existing zone devices that belong to this receiver
                zones_registered = False
                for zone_dev_id, zone in self.managed_zones.items():
                    zone_dev = zone.device
                    zone_receiver_id = int(zone_dev.pluginProps.get('sourceReceiver', '0'))
                    if zone_receiver_id == dev.id:
                        receiver.register_zone(zone_dev, zone)
                        zones_registered = True
                        self.logger.debug(f"Registered existing zone {zone_dev.name} with receiver {dev.name}")
                
                # If we registered zones, poll them now
                if zones_registered:
                    receiver.poll_all_zones()
                
                # Trigger state list refresh
                dev.stateListOrDisplayStateIdChanged()
                
                self.logger.debug(f"Receiver {dev.name} communication started")
                
            elif dev.deviceTypeId in ('nilesAudioZone', 'nilesAudioZoneDimmer'):
                # Update initial state
                dev.updateStateOnServer('isPoweredOn', value=False, uiValue='Starting')
                
                # Create zone device manager instance
                zone = NilesZone(self, dev)
                self.managed_zones[dev.id] = zone
                
                # Register with parent receiver if it's already running
                receiver_id = int(dev.pluginProps.get('sourceReceiver', '0'))
                if receiver_id in self.managed_receivers:
                    receiver = self.managed_receivers[receiver_id]
                    receiver.register_zone(dev, zone)
                    self.logger.debug(f"Registered zone {dev.name} with running receiver")
                    # Poll this zone immediately to get its status
                    receiver.poll_zone(int(dev.pluginProps.get('zoneNumber', '1')))
                else:
                    self.logger.debug(f"Receiver {receiver_id} not yet running, zone {dev.name} will register later")
                
                # Trigger state list refresh
                dev.stateListOrDisplayStateIdChanged()
                
                self.logger.debug(f"Zone {dev.name} communication started")
            
        except Exception as e:
            self.logger.error(f"Failed to start communication with {dev.name}: {e}")
            if dev.deviceTypeId == 'nilesAudioReceiver':
                dev.updateStateOnServer('connectionState', value='Error')
            else:
                dev.updateStateOnServer('isPoweredOn', value=False, uiValue='Error')

    def deviceStopComm(self, dev: indigo.Device) -> None:
        """
        Called when device communication should stop.
        
        Args:
            dev: The Indigo device to stop communication with
        """
        self.logger.info(f"Stopping communication with {dev.name}")
        
        try:
            if dev.deviceTypeId == 'nilesAudioReceiver':
                # Stop and remove receiver device manager
                if dev.id in self.managed_receivers:
                    receiver = self.managed_receivers[dev.id]
                    receiver.stop()
                    del self.managed_receivers[dev.id]
                
                # Update device state
                dev.setErrorStateOnServer("")
                dev.updateStateOnServer('connectionState', value='Disabled')
                
            elif dev.deviceTypeId in ('nilesAudioZone', 'nilesAudioZoneDimmer'):
                # Unregister from parent receiver
                receiver_id = int(dev.pluginProps.get('sourceReceiver', '0'))
                if receiver_id in self.managed_receivers:
                    self.managed_receivers[receiver_id].unregister_zone(dev)
                
                # Remove from zone devices
                if dev.id in self.managed_zones:
                    del self.managed_zones[dev.id]
                
                # Update device state
                dev.setErrorStateOnServer("")
                dev.updateStateOnServer('isPoweredOn', value=False, uiValue='Disabled')
            
            self.logger.debug(f"Device {dev.name} communication stopped")
            
        except Exception as e:
            self.logger.warning(f"Error stopping communication with {dev.name}: {e}")

    def didDeviceCommPropertyChange(self, orig_dev: indigo.Device,
                                    new_dev: indigo.Device) -> bool:
        """
        Check if device properties changed in a way that requires restart.
        
        Args:
            orig_dev: Original device state
            new_dev: New device state
            
        Returns:
            True if communication should be restarted
        """
        # Properties that require restart if changed
        if orig_dev.deviceTypeId == 'nilesAudioReceiver':
            restart_props = ['serialPort', 'zonePollInterval']
        else:
            restart_props = ['sourceReceiver', 'zoneNumber']
        
        for prop in restart_props:
            if orig_dev.pluginProps.get(prop) != new_dev.pluginProps.get(prop):
                self.logger.debug(f"Property {prop} changed, requiring restart")
                return True
        
        return False

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Action Callbacks
    # ========================================================================
    def execute_action(self, action: indigo.ActionGroup, dev: indigo.Device,
                       caller_waiting_for_result: bool = False) -> None:
        """
        Handle action execution for device-specific actions.
        
        This is the main callback method referenced in Actions.xml.
        
        Args:
            action: The action to execute
            dev: The target device
            caller_waiting_for_result: Whether caller is waiting for result
        """
        action_id = action.pluginTypeId
        props = action.props
        
        self.logger.debug(f"Executing action {action_id} on {dev.name}")
        
        try:
            # Handle receiver-wide actions
            if dev.deviceTypeId == 'nilesAudioReceiver':
                if dev.id not in self.managed_receivers:
                    self.logger.error(f"Receiver {dev.name} is not available")
                    return
                
                receiver = self.managed_receivers[dev.id]
                
                if action_id == "allZonesOff":
                    receiver.all_zones_off()
                    
                elif action_id == "muteAllZones":
                    receiver.mute_all_zones()
                    
                elif action_id == "tuneToStation":
                    station = props.get("stationNumber", "").strip()
                    if station:
                        receiver.tune_to_station(station)
                    
                else:
                    self.logger.warning(f"Unknown receiver action: {action_id}")
            
            # Handle zone-specific actions
            elif dev.deviceTypeId in ('nilesAudioZone', 'nilesAudioZoneDimmer'):
                # Get the parent receiver for this zone
                receiver_id = int(dev.pluginProps.get('sourceReceiver', '0'))
                if receiver_id not in self.managed_receivers:
                    self.logger.error(f"Receiver for zone {dev.name} is not available")
                    return
                
                receiver = self.managed_receivers[receiver_id]
                zone_number = int(dev.pluginProps.get('zoneNumber', '1'))
                
                if action_id == "changeZonePower":
                    power_on = props.get("powerState", "0") == "1"
                    current_source = dev.states.get("source", 1)
                    receiver.set_zone_power(zone_number, power_on, current_source)
                    
                elif action_id == "toggleZonePower":
                    current_power = dev.states.get("isPoweredOn", False)
                    current_source = dev.states.get("source", 1)
                    receiver.set_zone_power(zone_number, not current_power, current_source)
                    
                elif action_id == "changeZoneSource":
                    source = int(props.get("zoneSource", "1"))
                    receiver.set_zone_source(zone_number, source)
                    
                elif action_id == "setZoneVolume":
                    target_volume = int(props.get("volumeTarget", "0"))
                    current_volume = dev.states.get("volume", 0)
                    receiver.set_zone_volume(zone_number, target_volume, current_volume)
                    
                elif action_id == "adjustZoneVolume":
                    adjustment = int(props.get("volumeAdjustment", "0"))
                    receiver.adjust_zone_volume(zone_number, adjustment)
                    
                elif action_id == "setZoneMute":
                    muted = props.get("muteState", "0") == "1"
                    is_currently_muted = dev.states.get("isMuted", False)
                    receiver.set_zone_mute(zone_number, muted, is_currently_muted)
                    
                elif action_id == "toggleZoneMuteStatus":
                    receiver.toggle_zone_mute(zone_number)
                    
                else:
                    self.logger.warning(f"Unknown zone action: {action_id}")
                
        except Exception as e:
            self.logger.error(f"Error executing action {action_id}: {e}")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Standard Device Actions (On/Off/Brightness)
    # ========================================================================
    def actionControlDimmerRelay(self, action: indigo.ActionGroup, dev: indigo.Device) -> None:
        """
        Handle standard dimmer/relay actions for zone devices.
        
        Zone devices are defined as dimmers, so we implement brightness as volume control.
        
        Args:
            action: The dimmer action to execute
            dev: The target zone device
        """
        if dev.deviceTypeId != 'nilesAudioZoneDimmer':
            self.logger.warning(f"Dimmer action called on non-dimmer device: {dev.name}")
            return
        
        # Get the parent receiver for this zone
        receiver_id = int(dev.pluginProps.get('sourceReceiver', '0'))
        if receiver_id not in self.managed_receivers:
            self.logger.error(f"Receiver for zone {dev.name} is not available")
            return
        
        receiver = self.managed_receivers[receiver_id]
        zone_number = int(dev.pluginProps.get('zoneNumber', '1'))
        
        try:
            # === Turn On ===
            if action.deviceAction == indigo.kDimmerRelayAction.TurnOn:
                self.logger.debug(f"Turn on zone {dev.name}")
                current_source = dev.states.get("source", 1)
                receiver.set_zone_power(zone_number, True, current_source)
                
            # === Turn Off ===
            elif action.deviceAction == indigo.kDimmerRelayAction.TurnOff:
                self.logger.debug(f"Turn off zone {dev.name}")
                receiver.set_zone_power(zone_number, False)
                
            # === Toggle ===
            elif action.deviceAction == indigo.kDimmerRelayAction.Toggle:
                self.logger.debug(f"Toggle zone {dev.name}")
                current_power = dev.states.get("isPoweredOn", False)
                current_source = dev.states.get("source", 1)
                receiver.set_zone_power(zone_number, not current_power, current_source)
                
            # === Set Brightness (Volume) ===
            elif action.deviceAction == indigo.kDimmerRelayAction.SetBrightness:
                # Brightness 0-100 maps to volume 0-38
                brightness = action.actionValue
                volume = max(0, min(MAX_VOLUME, int(brightness * BRIGHTNESS_TO_VOLUME_FACTOR)))
                current_volume = dev.states.get("volume", 0)
                self.logger.debug(f"Set zone {dev.name} volume to {volume} (brightness {brightness})")
                receiver.set_zone_volume(zone_number, volume, current_volume)
                
            # === Brighten By (Volume Up) ===
            elif action.deviceAction == indigo.kDimmerRelayAction.BrightenBy:
                # Convert brightness adjustment to volume adjustment
                adjustment = action.actionValue
                current_brightness = dev.states.get("brightnessLevel", 0)
                new_brightness = min(100, current_brightness + adjustment)
                volume = max(0, min(MAX_VOLUME, int(new_brightness * BRIGHTNESS_TO_VOLUME_FACTOR)))
                current_volume = dev.states.get("volume", 0)
                self.logger.debug(f"Brighten zone {dev.name} by {adjustment} to volume {volume}")
                receiver.set_zone_volume(zone_number, volume, current_volume)
                
            # === Dim By (Volume Down) ===
            elif action.deviceAction == indigo.kDimmerRelayAction.DimBy:
                # Convert brightness adjustment to volume adjustment
                adjustment = action.actionValue
                current_brightness = dev.states.get("brightnessLevel", 0)
                new_brightness = max(0, current_brightness - adjustment)
                volume = max(0, min(MAX_VOLUME, int(new_brightness * BRIGHTNESS_TO_VOLUME_FACTOR)))
                current_volume = dev.states.get("volume", 0)
                self.logger.debug(f"Dim zone {dev.name} by {adjustment} to volume {volume}")
                receiver.set_zone_volume(zone_number, volume, current_volume)
                
            else:
                self.logger.warning(f"Unhandled dimmer action: {action.deviceAction}")
                
        except Exception as e:
            self.logger.error(f"Error handling dimmer action for {dev.name}: {e}")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Configuration UI Callbacks
    # ========================================================================
    def validateDeviceConfigUi(self, values_dict: indigo.Dict,
                               type_id: str, dev_id: int) -> Tuple[bool, indigo.Dict]:
        """
        Validate device configuration.
        
        Args:
            values_dict: Dialog values
            type_id: Device type ID
            dev_id: Device ID (0 for new device)
            
        Returns:
            Tuple of (valid, values_dict) or (False, values_dict, errors_dict)
        """
        errors_dict = indigo.Dict()
        
        if type_id == 'nilesAudioReceiver':
            # Validate serial port
            serial_port = values_dict.get("serialPort", "").strip()
            if not serial_port:
                errors_dict["serialPort"] = "Please select a serial port"
            
            # Validate poll interval
            try:
                poll_interval = int(values_dict.get("zonePollInterval", "300"))
                if poll_interval < 0 or poll_interval > 10000:
                    errors_dict["zonePollInterval"] = "Poll interval must be between 0 and 10000"
            except ValueError:
                errors_dict["zonePollInterval"] = "Please enter a valid number"
            
            # Set address for display
            values_dict["address"] = serial_port
                
        elif type_id in ('nilesAudioZone', 'nilesAudioZoneDimmer'):
            # Validate receiver selection
            receiver_id = values_dict.get("sourceReceiver", "")
            if not receiver_id:
                errors_dict["sourceReceiver"] = "Please select a receiver"
            
            # Validate zone number
            zone_number = values_dict.get("zoneNumber", "")
            if not zone_number:
                errors_dict["zoneNumber"] = "Please select a zone number"
            
            # Set address for display
            values_dict["address"] = f"Zone {zone_number}"
        
        if len(errors_dict) > 0:
            errors_dict["showAlertText"] = "Please correct the highlighted errors."
            return False, values_dict, errors_dict
        
        return True, values_dict

    def validatePrefsConfigUi(self, values_dict: indigo.Dict) -> Tuple[bool, indigo.Dict]:
        """
        Validate plugin preferences configuration.
        
        Args:
            values_dict: Dialog values
            
        Returns:
            Tuple of (valid, values_dict)
        """
        return True, values_dict

    def closedPrefsConfigUi(self, values_dict: indigo.Dict,
                            user_cancelled: bool) -> None:
        """
        Called when plugin prefs dialog closes.
        
        Args:
            values_dict: Final dialog values
            user_cancelled: True if user cancelled
        """
        if not user_cancelled:
            # Update debug level
            debug_level_str = values_dict.get('debugLevel', '0')
            self.debug_level = DEBUG_LEVEL_MAP.get(debug_level_str, logging.WARNING)
            self.indigo_log_handler.setLevel(self.debug_level)
            
            self.logger.info("Plugin preferences saved")
        else:
            self.logger.debug("Plugin preferences cancelled")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Dynamic List Generators
    # ========================================================================
    def getConfigDialogMenu(self, filter: str = "", values_dict: indigo.Dict = None,
                            type_id: str = "", target_id: int = 0) -> List[Tuple[str, str]]:
        """
        Get source options for action configuration dialogs.
        
        Called by Actions.xml to populate the source selection dropdown.
        
        Args:
            filter: Filter string (unused)
            values_dict: Current dialog values
            type_id: Action type ID
            target_id: Target device ID
            
        Returns:
            List of (source_number, source_label) tuples
        """
        source_options = []
        
        try:
            # Get the device to find source labels
            dev = indigo.devices.get(target_id)
            if not dev:
                return source_options
            
            # If it's a zone device, get the parent receiver's source labels
            if dev.deviceTypeId == 'nilesAudioZone':
                receiver_id = int(dev.pluginProps.get('sourceReceiver', '0'))
                dev = indigo.devices.get(receiver_id)
                if not dev:
                    return source_options
            
            # Build source list from receiver properties
            for x in range(1, 7):
                source_prop_name = f"source{x}Label"
                source_label = dev.pluginProps.get(source_prop_name, "")
                if source_label:
                    source_options.append((str(x), f"Source {x}: {source_label}"))
                    
        except Exception as e:
            self.logger.error(f"Error getting source options: {e}")
        
        return source_options

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Menu Item Callbacks
    # ========================================================================
    def send_arbitrary_command(self, values_dict: indigo.Dict,
                               type_id: str) -> Tuple[bool, indigo.Dict]:
        """
        Send an arbitrary command via menu item.
        
        Args:
            values_dict: Dialog values
            type_id: Type identifier
            
        Returns:
            Tuple of (success, values_dict) or (False, values_dict, errors_dict)
        """
        try:
            device_id_str = values_dict.get("targetDevice", "0")
            command = values_dict.get("commandToSend", "").strip()
            
            if device_id_str == "" or device_id_str == "0":
                error_dict = indigo.Dict()
                error_dict["targetDevice"] = "Please select a device"
                return False, values_dict, error_dict
                
            if command == "":
                error_dict = indigo.Dict()
                error_dict["commandToSend"] = "Enter command to send"
                return False, values_dict, error_dict
            
            device_id = int(device_id_str)
            if device_id in self.managed_receivers:
                receiver = self.managed_receivers[device_id]
                receiver.send_command(command)
                self.logger.info(f"Sent arbitrary command: {command}")
                return True, values_dict
            else:
                error_dict = indigo.Dict()
                error_dict["targetDevice"] = "Device not found or not running"
                return False, values_dict, error_dict
                
        except Exception as e:
            self.logger.error(f"Error sending arbitrary command: {e}")
            return False, values_dict

    def toggle_debug_enabled(self) -> None:
        """Toggle debug logging on/off."""
        if self.debug_level == logging.DEBUG:
            self.debug_level = logging.WARNING
            self.indigo_log_handler.setLevel(self.debug_level)
            self.pluginPrefs["debugLevel"] = "0"
            indigo.server.log("Debug logging disabled")
        else:
            self.debug_level = logging.DEBUG
            self.indigo_log_handler.setLevel(self.debug_level)
            self.pluginPrefs["debugLevel"] = "2"
            indigo.server.log("Debug logging enabled")

    def dump_device_details_to_log(self, values_dict: indigo.Dict,
                                   type_id: str) -> Tuple[bool, indigo.Dict]:
        """
        Dump device details to the event log.
        
        Args:
            values_dict: Dialog values
            type_id: Type identifier
            
        Returns:
            Tuple of (success, values_dict)
        """
        device_ids = values_dict.get("devicesToDump", [])
        
        for dev_id_str in device_ids:
            try:
                dev_id = int(dev_id_str)
                dev = indigo.devices[dev_id]
                
                indigo.server.log("")
                indigo.server.log(f"===== Device Details: {dev.name} =====")
                indigo.server.log(f"Device ID: {dev.id}")
                indigo.server.log(f"Device Type: {dev.deviceTypeId}")
                indigo.server.log(f"Enabled: {dev.enabled}")
                indigo.server.log(f"Address: {dev.address}")
                
                indigo.server.log("----- Plugin Properties -----")
                for key, value in dev.pluginProps.items():
                    indigo.server.log(f"  {key}: {value}")
                
                indigo.server.log("----- States -----")
                for key, value in dev.states.items():
                    indigo.server.log(f"  {key}: {value}")
                
                indigo.server.log("================================")
                
            except Exception as e:
                self.logger.error(f"Error dumping device {dev_id_str}: {e}")
        
        return True, values_dict

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Helper Methods
    # ========================================================================
    def _time_to_poll(self, dev: indigo.Device, receiver: NilesReceiver) -> bool:
        """
        Check if receiver is due for status polling.
        
        Args:
            dev: The Indigo device to check
            receiver: The NilesReceiver manager instance
            
        Returns:
            True if device should be polled
        """
        if not dev.enabled:
            return False
        
        poll_interval = int(dev.pluginProps.get("zonePollInterval", "300"))
        if poll_interval <= 0:
            return False  # Polling disabled
        
        # Check time since last update
        elapsed = time.time() - receiver.last_poll_time
        return elapsed >= poll_interval

    def get_zone_by_number(self, receiver_id: int, zone_number: int) -> Optional[NilesZone]:
        """
        Find a zone device by its receiver and zone number.
        
        Args:
            receiver_id: The parent receiver device ID
            zone_number: The zone number (1-18)
            
        Returns:
            The NilesZone instance if found, None otherwise
        """
        for zone_id, zone in self.managed_zones.items():
            zone_dev = zone.device
            if int(zone_dev.pluginProps.get('sourceReceiver', '0')) == receiver_id:
                if int(zone_dev.pluginProps.get('zoneNumber', '0')) == zone_number:
                    return zone
        return None

    # endregion
    # ========================================================================
