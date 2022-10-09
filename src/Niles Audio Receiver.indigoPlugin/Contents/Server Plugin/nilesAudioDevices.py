#! /usr/bin/env python
# -*- coding: utf-8 -*-
#/////////////////////////////////////////////////////////////////////////////////////////
#/////////////////////////////////////////////////////////////////////////////////////////
# Niles Audio Receiver Plugin by RogueProeliator <rp@rogueproeliator.com>
# 	See plugin.py for more plugin details and information
#/////////////////////////////////////////////////////////////////////////////////////////
#/////////////////////////////////////////////////////////////////////////////////////////

#/////////////////////////////////////////////////////////////////////////////////////////
# Python imports
#/////////////////////////////////////////////////////////////////////////////////////////
import os
import Queue
import re
import string
import sys
import threading

import indigo
import RPFramework


#/////////////////////////////////////////////////////////////////////////////////////////
# Constants and configuration variables
#/////////////////////////////////////////////////////////////////////////////////////////
CMD_CREATEZONESTATUSUPDATECOMMAND = "createZoneStatusUpdateCommands"
CMD_ACTIVATEZONEFORCOMMAND        = "activateZoneForCommand"


#/////////////////////////////////////////////////////////////////////////////////////////
# NilesAudioReceiver
#	Handles the communications and status of a Niles audio receiver which is connected
#	via the serial port
#/////////////////////////////////////////////////////////////////////////////////////////
class NilesAudioReceiverDevice(RPFramework.RPFrameworkTelnetDevice.RPFrameworkTelnetDevice):
	
	#/////////////////////////////////////////////////////////////////////////////////////
	# Class construction and destruction methods
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class receiving a command to start device
	# communication. The plugin will call other commands when needed, simply zero out the
	# member variables
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, plugin, device):
		super().__init__(plugin, device, connectionType=RPFramework.RPFrameworkTelnetDevice.CONNECTIONTYPE_SERIAL)
		self.activeControlZone = 0
		
		
	#/////////////////////////////////////////////////////////////////////////////////////
	# Processing and command functions
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will process the commands that are not processed automatically by the
	# base class; it will be called on a concurrent thread
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def handleUnmanagedCommandInQueue(self, ipConnection, rpCommand):
		if rpCommand.commandName == CMD_CREATEZONESTATUSUPDATECOMMAND:
			# create a set of commands to update the status of a single zone
			updateCommandList = []
			updateCommandList.append(self.createZoneActivateCommand(rpCommand.commandPayload))
			updateCommandList.append(self.createZoneStatusRequestCommand(rpCommand.commandPayload))
			self.queueDeviceCommands(updateCommandList)
		
		elif rpCommand.commandName == "createAllZonesStatusRequestCommands":
			# create a set of commands to update the status of all zones defined by the
			# plugin (as child devices)
			updateCommandList = []
			for zoneNumber in self.childDevices:
				updateCommandList.append(self.createZoneActivateCommand(zoneNumber))
				updateCommandList.append(self.createZoneStatusRequestCommand(zoneNumber))
			
			# queue up all of the commands at once (so they will run back to back)
			self.queueDeviceCommands(updateCommandList)
			
		elif rpCommand.commandName == CMD_ACTIVATEZONEFORCOMMAND:
			# this command will immediately activate the requested zone (per the payload)
			# for control if it is not already active
			if self.activeControlZone != int(rpCommand.commandPayload):
				self.hostPlugin.logger.threaddebug(f"Writing activate zone request for zone {rpCommand.commandPayload}")
				writeCommand = f"znc,4,{rpCommand.commandPayload}\r"
				ipConnection.write(writeCommand.encode("ascii"))
			else:
				self.hostPlugin.logger.threaddebug(f"Zone {rpCommand.commandPayload} already active, ignoring activate zone command for efficiency")
				
			# ensure that the delay is in place...
			if rpCommand.postCommandPause == 0.0:
				rpCommand.postCommandPause = 0.1
				
		elif rpCommand.commandName == "createAllZonesMuteCommands":
			# this command will be fired whenever the plugin needs to create the commands that will mute
			# all zones (must be done individually)
			muteCommandList = []
			for zoneNumber in self.childDevices:
				if self.childDevices[zoneNumber].indigoDevice.states["isPoweredOn"] == True and self.childDevices[zoneNumber].indigoDevice.states["isMuted"] == False:
					self.hostPlugin.logger.threaddebug(f"Mute All: muting zone {zoneNumber}")
					muteCommandList.append(RPFramework.RPFrameworkCommand.RPFrameworkCommand(RPFramework.RPFrameworkTelnetDevice.CMD_WRITE_TO_DEVICE, commandPayload=f"zsc,{zoneNumber},11", postCommandPause=0.1))
					muteCommandList.append(RPFramework.RPFrameworkCommand.RPFrameworkCommand(CMD_CREATEZONESTATUSUPDATECOMMAND, commandPayload=str(zoneNumber), postCommandPause=0.1))
			self.queueDeviceCommands(muteCommandList)
	
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will be called in order to generate the commands necessary to update
	# the status of a zone defined for this receiver
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def createZoneStatusRequestCommand(self, zoneNumber):
		return RPFramework.RPFrameworkCommand.RPFrameworkCommand(RPFramework.RPFrameworkTelnetDevice.CMD_WRITE_TO_DEVICE, commandPayload="znc,5", postCommandPause=0.1)
			
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine will be called in order to generate the commands necessary to activate
	# a zone (for control) on this receiver
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def createZoneActivateCommand(self, zoneNumber):	
		return RPFramework.RPFrameworkCommand.RPFrameworkCommand(CMD_ACTIVATEZONEFORCOMMAND, commandPayload=str(zoneNumber), postCommandPause=0.1)
			

	#/////////////////////////////////////////////////////////////////////////////////////
	# Custom Response Handlers
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This callback is made whenever the plugin has received the response to a status
	# request for a particular zone
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def zoneStatusResponseReceived(self, responseObj, rpCommand):
		# the response format is a comma-delimited list with the following values:
		# USC, 2, [ZONE], [SOURCE #], [0|1 - ON/OFF], [VOLUME], [0|1 MUTE], [BASE], [TREB]
		responseParser = re.compile(r'^usc,2,(?P<zone>\d+),(?P<source>\d+),(?P<onOff>0|1),(?P<volume>\d+),(?P<mute>0|1),(?P<base>\d+),(?P<treble>\d+)$', re.I)
		statusObj = responseParser.match(responseObj)
		statusInfo = statusObj.groupdict()
		
		# device status updates are expensive, so only do the update on statuses that are
		# different than current
		self.hostPlugin.logger.debug(f"Received status update for Zone {statusInfo['zone']}: {responseObj}")
		zoneDevice = self.childDevices[statusInfo["zone"]]

		# get the on/off status as this will determine what info we update; do not update it now
		# since our uiValue may change depending upon other conditions
		statusIsPoweredOn = statusInfo["onOff"] == "1"
		forceUIValueUpdate = False
		onOffUIValue = ""

		# we may only update the remainder of the states if the zone is powered on... otherwise
		# the information is not reliable
		if statusIsPoweredOn == True:
			zoneStatesToUpdate = []
		
			if zoneDevice.indigoDevice.states.get("source", "") != statusInfo["source"]:
				uiSourceValue = self.indigoDevice.pluginProps.get(f"source {statusInfo['source']} Label", "")
				if uiSourceValue == "":
					uiSourceValue = statusInfo["source"]
				zoneStatesToUpdate.append({ "key" : "source", "value" : int(statusInfo["source"]), "uiValue" : uiSourceValue})
		
			statusVolume = int(statusInfo["volume"])
			if int(zoneDevice.indigoDevice.states.get("volume", "0")) != statusVolume:
				forceUIValueUpdate = True
				zoneStatesToUpdate.append({ "key" : "volume", "value" : statusVolume})
			
			if zoneDevice.indigoDevice.states.get("isMuted", False) != (statusInfo["mute"] == "1"):
				forceUIValueUpdate = True
				zoneStatesToUpdate.append({ "key" : "isMuted", "value" : (statusInfo["mute"] == "1")})
			
			statusBaseLevel = int(statusInfo["base"])
			if int(zoneDevice.indigoDevice.states.get("baseLevel", "0")) != statusBaseLevel:
				zoneStatesToUpdate.append({ "key" : "baseLevel", "value" : statusBaseLevel})
			
			statusTrebleLevel = int(statusInfo["treble"])
			if int(zoneDevice.indigoDevice.states.get("trebleLevel", "0")) != statusTrebleLevel:
				zoneStatesToUpdate.append({ "key" : "trebleLevel", "value" : statusTrebleLevel})
				
			# determine the on/off display text
			if statusInfo["mute"] == "1" or statusVolume == 0:
				onOffUIValue = "muted"
			else:
				onOffUIValue = str(statusVolume)
				
			if len(zoneStatesToUpdate) > 0:
				zoneDevice.indigoDevice.updateStatesOnServer(zoneStatesToUpdate)
		else:
			onOffUIValue = "off"
			self.hostPlugin.logger.debug("Skipping status update for zone that is off")
			
		# finally update the on/off state...
		if zoneDevice.indigoDevice.states.get("isPoweredOn", False) != statusIsPoweredOn or forceUIValueUpdate == True:
			zoneDevice.indigoDevice.updateStateOnServer(key="isPoweredOn", value=statusIsPoweredOn, uiValue=onOffUIValue)
			
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This callback is made whenever the plugin has received the response to a request
	# to change the currently-active zone (for control/update commands)
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def activeControlZoneUpdated(self, responseObj, rpCommand):
		# the response format is a comma-delimited response: rznc,4,[zone]
		responseParser = re.compile(r'^rznc,4,(\d+)\s*$', re.I)
		matchObj = responseParser.match(responseObj)
		self.activeControlZone = int(matchObj.group(1))
		self.hostPlugin.logger.threaddebug(f"Updated active control zone to {matchObj.group(1)}")
				
		
#/////////////////////////////////////////////////////////////////////////////////////////
# NilesAudioZone
#	Handles the status and representation of a zone associated with a Niles Audio multi-
#	zone receiver
#/////////////////////////////////////////////////////////////////////////////////////////
class NilesAudioZone(RPFramework.RPFrameworkNonCommChildDevice.RPFrameworkNonCommChildDevice):

	#/////////////////////////////////////////////////////////////////////////////////////
	# Class construction and destruction methods
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# Constructor called once upon plugin class receiving a command to start device
	# communication. The plugin will call other commands when needed, simply zero out the
	# member variables
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def __init__(self, plugin, device):
		super().__init__(plugin, device)
		
		
	#/////////////////////////////////////////////////////////////////////////////////////
	# Validation and GUI functions
	#/////////////////////////////////////////////////////////////////////////////////////
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This routine is called to retrieve a dynamic list of elements for an action (or
	# other ConfigUI based) routine
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def getConfigDialogMenuItems(self, filter, valuesDict, typeId, targetId):
		# we need the parent (receiver) device in order to get the list of
		# available sources...
		parentReceiver = self.hostPlugin.managedDevices[int(self.indigoDevice.pluginProps["sourceReceiver"])]
		
		sourceOptions = []
		for x in range(1,7):
			sourcePropName = "source" + RPFramework.RPFrameworkUtils.to_unicode(x) + "Label"
			if parentReceiver.indigoDevice.pluginProps[sourcePropName] != "":
				sourceOptions.append((RPFramework.RPFrameworkUtils.to_unicode(x), f"Source {x}: {parentReceiver.indigoDevice.pluginProps[sourcePropName]}"))
			
		return sourceOptions
		
	