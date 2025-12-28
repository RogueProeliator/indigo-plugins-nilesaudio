#! /usr/bin/env python
# -*- coding: utf-8 -*-
"""
Niles Audio Receiver Device Class

Manages serial communication with a Niles Audio zone receiver (ZR-4/ZR-6).
This class handles serial port communication, command queuing, response parsing,
and state updates for the main receiver device.

Protocol reference: The Niles Audio ZR-4/ZR-6 uses a serial protocol where 
commands are sent as ASCII strings and responses are received in a specific format.

Command format varies by operation:
- Zone select control: znc,4,[zone]
- Zone status query: znc,5
- Zone set control: zsc,[zone],[function]
- Zone tune control: znt,10,h (all zones off)
- Source/tuner control: src,11,[station]

Response format:
- Zone status: usc,2,[zone],[source],[on/off],[volume],[mute],[bass],[treble]
- Zone activate response: rznc,4,[zone]
"""

# region Python Imports
import math
import re
import serial
import threading
import time
from dataclasses import dataclass
from enum import Enum
from queue import Queue, Empty
from typing import Dict, Optional, TYPE_CHECKING

import indigo

if TYPE_CHECKING:
    from plugin import Plugin
    from niles_zone import NilesZone
# endregion

# region Constants
# Niles Audio volume range is 0-38 (same as Dayton Audio)
MAX_VOLUME = 38
VOLUME_TO_BRIGHTNESS_FACTOR = 100.0 / MAX_VOLUME  # ~2.63
BRIGHTNESS_TO_VOLUME_FACTOR = MAX_VOLUME / 100.0  # 0.38
# endregion


class CommandType(Enum):
    """Types of commands that can be queued."""
    WRITE = "write"
    POLL_ALL = "poll_all"
    POLL_ZONE = "poll_zone"
    ACTIVATE_ZONE = "activate_zone"
    MUTE_ALL = "mute_all"


@dataclass
class Command:
    """A command to be executed on the receiver."""
    command_type: CommandType
    payload: str = ""
    zone_number: int = 0
    repeat_count: int = 1
    repeat_delay: float = 0.1


class NilesReceiver:
    """
    Manages communication with a Niles Audio zone receiver.
    
    Features:
    - Serial port communication at 38400 baud
    - Threaded command processing via queue
    - Status polling for all zones
    - Zone state tracking and updates
    - Active control zone management (required by Niles protocol)
    
    The Niles receiver protocol requires activating a zone before sending
    commands to it. This is tracked via the active_control_zone variable.
    """
    
    # Serial port configuration - Niles uses 38400 baud
    BAUD_RATE = 38400
    BYTE_SIZE = serial.EIGHTBITS
    PARITY = serial.PARITY_NONE
    STOP_BITS = serial.STOPBITS_ONE
    READ_TIMEOUT = 0.5
    WRITE_TIMEOUT = 1.0
    
    # Command timing
    COMMAND_PAUSE = 0.1

    def __init__(self, plugin: 'Plugin', device: indigo.Device):
        """
        Initialize the Niles receiver manager.
        
        Args:
            plugin: Reference to the main plugin instance
            device: The Indigo device this manager controls
        """
        self.host_plugin = plugin
        self.device_id = device.id  # Store ID, not device object (can become stale)
        self.logger = plugin.logger
        
        # Serial port configuration
        self.serial_port_name = device.pluginProps.get('serialPort', '')
        
        # Serial connection
        self.serial_conn: Optional[serial.Serial] = None
        
        # Threading infrastructure
        self.queue: Queue = Queue()
        self.thread: Optional[threading.Thread] = None
        self._stop_thread = False
        self._lock = threading.Lock()
        
        # Zone tracking - maps zone number (string) to NilesZone instance
        self.registered_zones: Dict[str, 'NilesZone'] = {}
        
        # Status tracking
        self.last_poll_time: float = 0
        self.is_connected: bool = False
        
        # Niles-specific: track which zone is currently active for control
        self.active_control_zone: int = 0
        
        # Response parsing regex patterns
        # Zone status response: usc,2,[zone],[source],[on/off],[volume],[mute],[bass],[treble]
        self.zone_status_pattern = re.compile(
            r'^usc,2,(?P<zone>\d+),(?P<source>\d+),(?P<onOff>0|1),'
            r'(?P<volume>\d+),(?P<mute>0|1),(?P<base>\d+),(?P<treble>\d+)\s*$',
            re.IGNORECASE | re.MULTILINE
        )
        
        # Zone activate response: rznc,4,[zone]
        self.zone_activate_pattern = re.compile(
            r'^rznc,4,(\d+)\s*$',
            re.IGNORECASE | re.MULTILINE
        )
        
        self.logger.debug(f"NilesReceiver initialized for {device.name}")

    @property
    def device(self) -> indigo.Device:
        """Get a fresh device reference from Indigo."""
        return indigo.devices[self.device_id]

    # ========================================================================
    # region Lifecycle Methods
    # ========================================================================
    def start(self) -> None:
        """Start the device communication thread and open serial port."""
        self._stop_thread = False
        
        # Open serial connection
        if not self._open_serial():
            self.device.updateStateOnServer('connectionState', value='Error')
            self.device.updateStateOnServer('isConnected', value=False)
            return
        
        # Start processing thread
        self.thread = threading.Thread(
            target=self._process_queue,
            name=f"Niles-{self.device.id}",
            daemon=True
        )
        self.thread.start()
        
        self.logger.debug(f"Receiver thread started for {self.device.name}")
        
        # Queue initial poll
        self.poll_all_zones()

    def stop(self) -> None:
        """Stop the device communication thread and close serial port."""
        self._stop_thread = True
        
        # Add a None command to wake up the thread
        self.queue.put(None)
        
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        # Close serial connection
        self._close_serial()
        
        self.logger.debug(f"Receiver thread stopped for {self.device.name}")

    def _open_serial(self) -> bool:
        """
        Open the serial port connection.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.serial_conn = serial.Serial(
                port=self.serial_port_name,
                baudrate=self.BAUD_RATE,
                bytesize=self.BYTE_SIZE,
                parity=self.PARITY,
                stopbits=self.STOP_BITS,
                timeout=self.READ_TIMEOUT,
                write_timeout=self.WRITE_TIMEOUT
            )
            
            self.is_connected = True
            self.device.updateStateOnServer('connectionState', value='Connected')
            self.device.updateStateOnServer('isConnected', value=True)
            self.logger.info(f"Serial port {self.serial_port_name} opened for {self.device.name}")
            return True
            
        except serial.SerialException as e:
            self.logger.error(f"Failed to open serial port {self.serial_port_name}: {e}")
            self.is_connected = False
            return False

    def _close_serial(self) -> None:
        """Close the serial port connection."""
        if self.serial_conn and self.serial_conn.is_open:
            try:
                self.serial_conn.close()
                self.logger.debug(f"Serial port {self.serial_port_name} closed")
            except Exception as e:
                self.logger.warning(f"Error closing serial port: {e}")
        
        self.serial_conn = None
        self.is_connected = False
        self.device.updateStateOnServer('isConnected', value=False)

    def _process_queue(self) -> None:
        """Main thread loop - processes commands from queue."""
        while not self._stop_thread:
            try:
                command = self.queue.get(timeout=0.5)
                
                if command is None:
                    continue
                
                self._execute_command(command)
                
            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Error processing queue for {self.device.name}: {e}")

    def _execute_command(self, command: Command) -> None:
        """
        Execute a command from the queue.
        
        Args:
            command: The command to execute
        """
        try:
            if command.command_type == CommandType.WRITE:
                # Handle repeat commands (used for volume adjustments)
                for i in range(command.repeat_count):
                    self._do_write(command.payload)
                    if i < command.repeat_count - 1:
                        time.sleep(command.repeat_delay)
                
            elif command.command_type == CommandType.POLL_ALL:
                self._do_poll_all()
                
            elif command.command_type == CommandType.POLL_ZONE:
                self._do_poll_zone(command.zone_number)
                
            elif command.command_type == CommandType.ACTIVATE_ZONE:
                self._do_activate_zone(command.zone_number)
                
            elif command.command_type == CommandType.MUTE_ALL:
                self._do_mute_all()
                
        except Exception as e:
            self.logger.error(f"Error executing {command.command_type}: {e}")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Zone Registration
    # ========================================================================
    def register_zone(self, dev: indigo.Device, zone: 'NilesZone') -> None:
        """
        Register a zone device with this receiver.
        
        Args:
            dev: The Indigo zone device
            zone: The NilesZone instance
        """
        zone_number = dev.pluginProps.get('zoneNumber', '0')
        self.registered_zones[zone_number] = zone
        self.logger.debug(f"Registered zone {zone_number} ({dev.name})")

    def unregister_zone(self, dev: indigo.Device) -> None:
        """
        Unregister a zone device from this receiver.
        
        Args:
            dev: The Indigo zone device
        """
        zone_number = dev.pluginProps.get('zoneNumber', '0')
        if zone_number in self.registered_zones:
            del self.registered_zones[zone_number]
            self.logger.debug(f"Unregistered zone {zone_number} ({dev.name})")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Public Command Methods
    # ========================================================================
    def send_command(self, command: str, repeat_count: int = 1, repeat_delay: float = 0.1) -> None:
        """
        Queue a raw command to send to the receiver.
        
        Args:
            command: The raw command string
            repeat_count: Number of times to repeat the command
            repeat_delay: Delay between repeated commands
        """
        self.queue.put(Command(CommandType.WRITE, payload=command, 
                               repeat_count=repeat_count, repeat_delay=repeat_delay))
        self.logger.debug(f"Queued command: {command} (repeat={repeat_count})")

    def poll_all_zones(self) -> None:
        """Queue a poll command for all registered zones."""
        self.queue.put(Command(CommandType.POLL_ALL))
        self.logger.debug("Queued poll for all zones")

    def poll_zone(self, zone_number: int) -> None:
        """
        Queue a poll command for a specific zone.
        
        Args:
            zone_number: The zone number (1-18)
        """
        self.queue.put(Command(CommandType.POLL_ZONE, zone_number=zone_number))

    # Zone Control Methods
    def set_zone_power(self, zone_number: int, power_on: bool, current_source: int = 1) -> None:
        """
        Set power state for a specific zone.
        
        The Niles protocol uses source selection for power on and function 10 for power off.
        
        Args:
            zone_number: The zone number (1-18)
            power_on: True to power on, False to power off
            current_source: The current/desired source when powering on
        """
        if power_on:
            # Power on by selecting a source
            self.send_command(f"zsc,{zone_number},{current_source}")
        else:
            # Power off using function code 10
            self.send_command(f"zsc,{zone_number},10")
        self.poll_zone(zone_number)

    def set_zone_source(self, zone_number: int, source: int) -> None:
        """
        Set source for a specific zone (1-6).
        
        Selecting a source also powers on the zone.
        """
        source = max(1, min(6, source))
        self.send_command(f"zsc,{zone_number},{source}")
        self.poll_zone(zone_number)

    def set_zone_volume(self, zone_number: int, target_volume: int, current_volume: int) -> None:
        """
        Set volume for a specific zone.
        
        The Niles protocol uses increment/decrement commands (12/13) rather than
        absolute volume setting. This calculates the number of steps needed.
        
        Args:
            zone_number: The zone number (1-18)
            target_volume: Target volume level (0-38)
            current_volume: Current volume level (0-38)
        """
        target_volume = max(0, min(MAX_VOLUME, target_volume))
        diff = target_volume - current_volume
        
        if diff == 0:
            return
        
        # 12 = volume up, 13 = volume down
        function = "12" if diff > 0 else "13"
        steps = abs(diff)
        
        self.send_command(f"zsc,{zone_number},{function}", repeat_count=steps, repeat_delay=0.1)
        self.poll_zone(zone_number)

    def adjust_zone_volume(self, zone_number: int, adjustment: int) -> None:
        """
        Adjust volume for a specific zone up or down.
        
        Args:
            zone_number: The zone number (1-18)
            adjustment: Volume adjustment (+/- steps)
        """
        if adjustment == 0:
            return
        
        # 12 = volume up, 13 = volume down
        function = "12" if adjustment > 0 else "13"
        steps = abs(adjustment)
        
        self.send_command(f"zsc,{zone_number},{function}", repeat_count=steps, repeat_delay=0.1)
        self.poll_zone(zone_number)

    def set_zone_mute(self, zone_number: int, muted: bool, is_currently_muted: bool) -> None:
        """
        Set mute state for a specific zone.
        
        The Niles protocol toggles mute with function 11, so we need to know
        the current state to decide whether to send the command.
        
        Args:
            zone_number: The zone number (1-18)
            muted: Desired mute state
            is_currently_muted: Current mute state
        """
        # Only send command if we need to change state
        if muted != is_currently_muted:
            self.send_command(f"zsc,{zone_number},11")
            self.poll_zone(zone_number)

    def toggle_zone_mute(self, zone_number: int) -> None:
        """Toggle mute state for a specific zone."""
        self.send_command(f"zsc,{zone_number},11")
        self.poll_zone(zone_number)

    # Receiver-wide Control Methods
    def all_zones_off(self) -> None:
        """Turn off all zones."""
        self.send_command("znt,10,h")
        self.poll_all_zones()

    def mute_all_zones(self) -> None:
        """Mute all zones that are currently powered on and not muted."""
        self.queue.put(Command(CommandType.MUTE_ALL))

    def tune_to_station(self, station: str) -> None:
        """
        Tune the built-in tuner to a radio station.
        
        Args:
            station: Station number in FM (###.#) or AM (####) format
        """
        self.send_command(f"src,11,{station}")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Command Implementations
    # ========================================================================
    def _do_write(self, command: str) -> None:
        """
        Write a command to the serial port.
        
        Args:
            command: The command string to write
        """
        if not self.serial_conn or not self.serial_conn.is_open:
            self.logger.warning("Serial port not open, cannot send command")
            return
        
        try:
            with self._lock:
                # Send command with carriage return
                cmd_bytes = (command + "\r").encode('ascii')
                self.serial_conn.write(cmd_bytes)
                self.logger.debug(f"Sent: {command}")
                
                # Small pause after write
                time.sleep(self.COMMAND_PAUSE)
                
                # Read any response
                self._read_response()
                
        except serial.SerialException as e:
            self.logger.error(f"Serial write error: {e}")
            self._handle_connection_error()

    def _do_poll_all(self) -> None:
        """Poll status for all registered zones."""
        for zone_number_str in self.registered_zones:
            zone_number = int(zone_number_str)
            self._do_activate_zone(zone_number)
            time.sleep(self.COMMAND_PAUSE)
            self._do_query_zone_status()
            time.sleep(self.COMMAND_PAUSE)
        
        self.last_poll_time = time.time()

    def _do_poll_zone(self, zone_number: int) -> None:
        """
        Poll status for a specific zone.
        
        Args:
            zone_number: The zone number (1-18)
        """
        self._do_activate_zone(zone_number)
        time.sleep(self.COMMAND_PAUSE)
        self._do_query_zone_status()

    def _do_activate_zone(self, zone_number: int) -> None:
        """
        Activate a zone for control commands.
        
        The Niles protocol requires activating a zone before querying or
        controlling it. This sends znc,4,[zone] and waits for the response.
        
        Args:
            zone_number: The zone number to activate
        """
        if self.active_control_zone == zone_number:
            self.logger.debug(f"Zone {zone_number} already active, skipping activation")
            return
        
        self.logger.debug(f"Activating zone {zone_number} for control")
        self._do_write(f"znc,4,{zone_number}")

    def _do_query_zone_status(self) -> None:
        """Query status for the currently active zone."""
        self._do_write("znc,5")

    def _do_mute_all(self) -> None:
        """Mute all zones that are currently powered on and not muted."""
        for zone_number_str, zone in self.registered_zones.items():
            dev = zone.device
            if dev.states.get("isPoweredOn", False) and not dev.states.get("isMuted", False):
                self.logger.debug(f"Mute All: muting zone {zone_number_str}")
                self._do_write(f"zsc,{zone_number_str},11")
                time.sleep(self.COMMAND_PAUSE)
        
        # Poll all zones to update status
        self._do_poll_all()

    def _read_response(self) -> None:
        """Read and process response from serial port."""
        if not self.serial_conn or not self.serial_conn.is_open:
            return
        
        try:
            # Read available data
            response = ""
            while self.serial_conn.in_waiting > 0:
                chunk = self.serial_conn.read(self.serial_conn.in_waiting).decode('ascii', errors='ignore')
                response += chunk
                time.sleep(0.05)  # Small delay to allow more data to arrive
            
            if response:
                self.logger.debug(f"Received: {response.strip()}")
                self._parse_response(response)
                
        except serial.SerialException as e:
            self.logger.error(f"Serial read error: {e}")

    def _parse_response(self, response: str) -> None:
        """
        Parse responses from the receiver.
        
        Handles two response types:
        - Zone status: usc,2,[zone],[source],[on/off],[volume],[mute],[bass],[treble]
        - Zone activate: rznc,4,[zone]
        
        Args:
            response: The response string from the receiver
        """
        # Check for zone activate response
        for match in self.zone_activate_pattern.finditer(response):
            zone_number = int(match.group(1))
            self.active_control_zone = zone_number
            self.logger.debug(f"Updated active control zone to {zone_number}")
        
        # Check for zone status response
        for match in self.zone_status_pattern.finditer(response):
            status = match.groupdict()
            zone_number = status["zone"]
            
            self.logger.debug(f"Parsed status for Zone {zone_number}: {status}")
            
            # Find and update the zone device
            if zone_number in self.registered_zones:
                zone = self.registered_zones[zone_number]
                self._update_zone_states(zone, status)
            else:
                self.logger.debug(f"Zone {zone_number} not registered, skipping update")

    def _update_zone_states(self, zone: 'NilesZone', status: Dict[str, str]) -> None:
        """
        Update zone device states from parsed response.
        
        For dimmer devices (nilesAudioZoneDimmer), also updates brightnessLevel
        and onOffState to support native dimmer controls.
        
        Args:
            zone: The NilesZone instance to update
            status: Dictionary of parsed status values
        """
        dev = zone.device
        
        # Parse values
        is_powered_on = status["onOff"] == "1"
        volume = int(status["volume"])
        is_muted = status["mute"] == "1"
        source = int(status["source"])
        bass_level = int(status["base"])
        treble_level = int(status["treble"])
        
        # Determine the UI display value for isPoweredOn
        if not is_powered_on:
            ui_value = "off"
        elif is_muted or volume == 0:
            ui_value = "muted"
        else:
            ui_value = str(volume)
        
        # Build state updates - only update if powered on for most states
        states = []
        
        if is_powered_on:
            # Get source label from receiver device properties
            source_label = self.device.pluginProps.get(f"source{source}Label", "")
            if not source_label:
                source_label = str(source)
            
            if dev.states.get("source", 0) != source:
                states.append({"key": "source", "value": source, "uiValue": source_label})
            
            if int(dev.states.get("volume", 0)) != volume:
                states.append({"key": "volume", "value": volume})
            
            if dev.states.get("isMuted", False) != is_muted:
                states.append({"key": "isMuted", "value": is_muted})
            
            if int(dev.states.get("bassLevel", 0)) != bass_level:
                states.append({"key": "bassLevel", "value": bass_level})
            
            if int(dev.states.get("trebleLevel", 0)) != treble_level:
                states.append({"key": "trebleLevel", "value": treble_level})
        else:
            self.logger.debug("Skipping detailed status update for zone that is off")
        
        # Update all collected states
        if states:
            dev.updateStatesOnServer(states)
        
        # Always update power state (may need uiValue update even if value unchanged)
        current_power = dev.states.get("isPoweredOn", False)
        if current_power != is_powered_on or True:  # Always update for uiValue
            dev.updateStateOnServer(key="isPoweredOn", value=is_powered_on, uiValue=ui_value)
        
        # For dimmer devices, also update the standard dimmer states (brightnessLevel, onOffState)
        # This allows native Indigo dimmer controls to work with volume
        if dev.deviceTypeId == 'nilesAudioZoneDimmer':
            # brightnessLevel: volume 0-38 maps to brightness 0-100
            if is_powered_on:
                brightness = int(math.floor(volume * VOLUME_TO_BRIGHTNESS_FACTOR))
            else:
                brightness = 0
            dev.updateStateOnServer(key="brightnessLevel", value=brightness)
            
            # onOffState: on if powered on
            dev.updateStateOnServer(key="onOffState", value=is_powered_on)
        
        self.logger.debug(f"Updated states for {dev.name}")

    # endregion
    # ========================================================================
    
    # ========================================================================
    # region Helper Methods
    # ========================================================================
    def _handle_connection_error(self) -> None:
        """Handle serial connection error."""
        self.is_connected = False
        self.device.updateStateOnServer('connectionState', value='Error')
        self.device.updateStateOnServer('isConnected', value=False)
        
        # Try to reconnect
        self._close_serial()
        time.sleep(1)
        self._open_serial()

    # endregion
    # ========================================================================
