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
CMD_ACTIVATEZONEFORCOMMAND = "activateZoneForCommand"


#/////////////////////////////////////////////////////////////////////////////////////////
#/////////////////////////////////////////////////////////////////////////////////////////
# NilesAudioReceiver
#	Handles the communications and status of a Niles audio receiver which is connected
#	via the serial port
#/////////////////////////////////////////////////////////////////////////////////////////
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
		super(NilesAudioReceiverDevice, self).__init__(plugin, device, connectionType=RPFramework.RPFrameworkTelnetDevice.CONNECTIONTYPE_SERIAL)
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
		
		elif rpCommand.commandName == u'createAllZonesStatusRequestCommands':
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
				self.hostPlugin.logDebugMessage(u'Writing activate zone request for zone ' + RPFramework.RPFrameworkUtils.to_unicode(rpCommand.commandPayload), RPFramework.RPFrameworkPlugin.DEBUGLEVEL_HIGH)
				writeCommand = "znc,4," + rpCommand.commandPayload + "\r"
				ipConnection.write(writeCommand.encode("ascii"))
			else:
				self.hostPlugin.logDebugMessage(u'Zone ' + RPFramework.RPFrameworkUtils.to_unicode(rpCommand.commandPayload) + u' already active, ignoring activate zone command for efficiency', RPFramework.RPFrameworkPlugin.DEBUGLEVEL_HIGH)
				
			# ensure that the delay is in place...
			if rpCommand.postCommandPause == 0.0:
				rpCommand.postCommandPause = 0.1
				
		elif rpCommand.commandName == u'createAllZonesMuteCommands':
			# this command will be fired whenever the plugin needs to create the commands that will mute
			# all zones (must be done individually)
			muteCommandList = []
			for zoneNumber in self.childDevices:
				if self.childDevices[zoneNumber].indigoDevice.states[u'isPoweredOn'] == True and self.childDevices[zoneNumber].indigoDevice.states[u'isMuted'] == False:
					self.hostPlugin.logDebugMessage("Mute All: muting zone " + str(zoneNumber), RPFramework.RPFrameworkPlugin.DEBUGLEVEL_HIGH)
					muteCommandList.append(RPFramework.RPFrameworkCommand.RPFrameworkCommand(RPFramework.RPFrameworkTelnetDevice.CMD_WRITE_TO_DEVICE, commandPayload="zsc," + zoneNumber + ",11", postCommandPause=0.1))
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
		self.hostPlugin.logDebugMessage(u'Received status update for Zone ' + RPFramework.RPFrameworkUtils.to_unicode(statusInfo["zone"]) + u': ' + RPFramework.RPFrameworkUtils.to_unicode(responseObj), RPFramework.RPFrameworkPlugin.DEBUGLEVEL_MED)
		zoneDevice = self.childDevices[statusInfo["zone"]]

		# get the on/off status as this will determine what info we update; do not update it now
		# since our uiValue may change depending upon other conditions
		statusIsPoweredOn = statusInfo[u'onOff'] == u'1'
		forceUIValueUpdate = False
		onOffUIValue = u''

		# we may only update the remainder of the states if the zone is powered on... otherwise
		# the information is not reliable
		if statusIsPoweredOn == True:
			if zoneDevice.indigoDevice.states.get(u'source', u'') != statusInfo[u'source']:
				uiSourceValue = self.indigoDevice.pluginProps.get(u'source' + statusInfo[u'source'] + u'Label', u'')
				if uiSourceValue == "":
					uiSourceValue = statusInfo["source"]
				zoneDevice.indigoDevice.updateStateOnServer(key=u'source', value=int(statusInfo[u'source']), uiValue=uiSourceValue)
		
			statusVolume = int(statusInfo[u'volume'])
			if int(zoneDevice.indigoDevice.states.get(u'volume', u'0')) != statusVolume:
				forceUIValueUpdate = True
				zoneDevice.indigoDevice.updateStateOnServer(key=u'volume', value=statusVolume)
			
			if zoneDevice.indigoDevice.states.get(u'isMuted', False) != (statusInfo[u'mute'] == u'1'):
				forceUIValueUpdate = True
				zoneDevice.indigoDevice.updateStateOnServer(key=u'isMuted', value=(statusInfo[u'mute'] == u'1'))
			
			statusBaseLevel = int(statusInfo[u'base'])
			if int(zoneDevice.indigoDevice.states.get(u'baseLevel', u'0')) != statusBaseLevel:
				zoneDevice.indigoDevice.updateStateOnServer(key="baseLevel", value=statusBaseLevel)
			
			statusTrebleLevel = int(statusInfo[u'treble'])
			if int(zoneDevice.indigoDevice.states.get(u'trebleLevel', u'0')) != statusTrebleLevel:
				zoneDevice.indigoDevice.updateStateOnServer(key=u'trebleLevel', value=statusTrebleLevel)
				
			# determine the on/off display text
			if statusInfo[u'mute'] == u'1' or statusVolume == 0:
				onOffUIValue = u'muted'
			else:
				onOffUIValue = str(statusVolume)
		else:
			onOffUIValue = u'off'
			self.hostPlugin.logDebugMessage(u'Skipping status update for zone that is off', RPFramework.RPFrameworkPlugin.DEBUGLEVEL_MED)
			
		# finally update the on/off state...
		if zoneDevice.indigoDevice.states.get(u'isPoweredOn', False) != statusIsPoweredOn or forceUIValueUpdate == True:
			zoneDevice.indigoDevice.updateStateOnServer(key=u'isPoweredOn', value=statusIsPoweredOn, uiValue=onOffUIValue)
			
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	# This callback is made whenever the plugin has received the response to a request
	# to change the currently-active zone (for control/update commands)
	#-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-=-
	def activeControlZoneUpdated(self, responseObj, rpCommand):
		# the response format is a comma-delimited response: rznc,4,[zone]
		responseParser = re.compile(r'^rznc,4,(\d+)\s*$', re.I)
		matchObj = responseParser.match(responseObj)
		self.activeControlZone = int(matchObj.group(1))
		self.hostPlugin.logDebugMessage(u'Updated active control zone to ' + RPFramework.RPFrameworkUtils.to_unicode(matchObj.group(1)), RPFramework.RPFrameworkPlugin.DEBUGLEVEL_HIGH)
				
		
#/////////////////////////////////////////////////////////////////////////////////////////
#/////////////////////////////////////////////////////////////////////////////////////////
# NilesAudioZone
#	Handles the status and representation of a zone associated with a Niles Audio multi-
#	zone receiver
#/////////////////////////////////////////////////////////////////////////////////////////
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
		super(NilesAudioZone, self).__init__(plugin, device)
		
		
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
			sourcePropName = u'source' + RPFramework.RPFrameworkUtils.to_unicode(x) + u'Label'
			if parentReceiver.indigoDevice.pluginProps[sourcePropName] != "":
				sourceOptions.append((RPFramework.RPFrameworkUtils.to_unicode(x), u'Source ' + RPFramework.RPFrameworkUtils.to_unicode(x) + u': ' + parentReceiver.indigoDevice.pluginProps[sourcePropName]))
			
		return sourceOptions
		
	