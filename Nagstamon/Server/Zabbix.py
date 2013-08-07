#!/usr/bin/python
# -*- encoding: utf-8; py-indent-offset: 4 -*-
# +------------------------------------------------------------------+
# |             ____ _               _        __  __ _  __           |
# |            / ___| |__   ___  ___| | __   |  \/  | |/ /           |
# |           | |   | '_ \ / _ \/ __| |/ /   | |\/| | ' /            |
# |           | |___| | | |  __/ (__|   <    | |  | | . \            |
# |            \____|_| |_|\___|\___|_|\_\___|_|  |_|_|\_\           |
# |                                                                  |
# | Copyright Mathias Kettner 2010             mk@mathias-kettner.de |
# |                                            lm@mathias-kettner.de |
# +------------------------------------------------------------------+
#
# The official homepage is at http://mathias-kettner.de/check_mk.
#
# check_mk is free software;  you can redistribute it and/or modify it
# under the  terms of the  GNU General Public License  as published by
# the Free Software Foundation in version 2.  check_mk is  distributed
# in the hope that it will be useful, but WITHOUT ANY WARRANTY;  with-
# out even the implied warranty of  MERCHANTABILITY  or  FITNESS FOR A
# PARTICULAR PURPOSE. See the  GNU General Public License for more de-
# ails.  You should have  received  a copy of the  GNU  General Public
# License along with GNU Make; see the file  COPYING.  If  not,  write
# to the Free Software Foundation, Inc., 51 Franklin St,  Fifth Floor,
# Boston, MA 02110-1301 USA.

# hax0rized by: lm@mathias-kettner.de

import sys
import urllib
import webbrowser
import traceback
import base64
import time
import datetime

from Nagstamon import Actions
from Nagstamon.Objects import *
from Nagstamon.Server.Generic import GenericServer
from Nagstamon.zabbix_api import ZabbixAPI, ZabbixAPIException


class ZabbixError(Exception):
    def __init__(self, terminate, result):
        self.terminate = terminate
        self.result = result


class ZabbixServer(GenericServer):
    """
       special treatment for Check_MK Multisite JSON API
    """
    TYPE = 'Zabbix'
    zapi = None

    def __init__(self, **kwds):
        GenericServer.__init__(self, **kwds)

        # Prepare all urls needed by nagstamon - 
        self.urls = {}
        self.statemap = {}

        # Entries for monitor default actions in context menu
        self.MENU_ACTIONS = ["Recheck", "Acknowledge", "Downtime"]
        self.username = self.conf.servers[self.get_name()].username
        self.password = self.conf.servers[self.get_name()].password

    def _login(self):
        try:
            self.zapi = ZabbixAPI(server=self.monitor_url, path="", log_level=0)
            self.zapi.login(self.username, self.password)
        except ZabbixAPIException:
            result, error = self.Error(sys.exc_info())
            return Result(result=result, error=error)

    def init_HTTP(self):
        # Fix eventually missing tailing "/" in url
        self.statemap = {
            'UNREACH': 'UNREACHABLE',
            'CRIT': 'CRITICAL',
            'WARN': 'WARNING',
            'UNKN': 'UNKNOWN',
            'PEND': 'PENDING',
            '0': 'OK',
            '1': 'WARNING',
            '2': 'WARNING',
            '5': 'CRITICAL',
            '3': 'WARNING',
            '4': 'CRITICAL'}
        GenericServer.init_HTTP(self)

    def _get_status(self):
        """
        Get status from Nagios Server
        """
        ret = Result()
        # create Nagios items dictionary with to lists for services and hosts
        # every list will contain a dictionary for every failed service/host
        # this dictionary is only temporarily
        nagitems = {"services": [], "hosts": []}

        # Create URLs for the configured filters
        if self.zapi is None:
            self._login()

        try:
            hosts = []
            try:
                hosts = self.zapi.host.get(
                    {"output": ["host", "ip", "status", "available", "error", "errors_from"], "filter": {}})

            except ZabbixError, e:
                if e.terminate:
                    return e.result

            for host in hosts:
                # johncan
                # self.Debug(str(host))
                duration = int(time.time()) - int(host['errors_from'])
                n = {
                    'host': host['host'],
                    'status': self.statemap.get(host['available'], host['available']),
                    'last_check': int(time.time()),
                    'duration': duration,
                    'status_information': host['error'],
                    'attempt': '0/0',
                    'site': '',
                    'address': host['host'],
                }

                # add dictionary full of information about this host item to nagitems
                nagitems["hosts"].append(n)
                # after collection data in nagitems create objects from its informations
                # host objects contain service objects
                if n["host"] not in self.new_hosts:
                    new_host = n["host"]
                    self.new_hosts[new_host] = GenericHost()
                    self.new_hosts[new_host].name = n["host"]
                    self.new_hosts[new_host].status = n["status"]
                    self.new_hosts[new_host].last_check = n["last_check"]
                    self.new_hosts[new_host].duration = n["duration"]
                    self.new_hosts[new_host].attempt = n["attempt"]
                    self.new_hosts[new_host].status_information = n["status_information"]
                    self.new_hosts[new_host].site = n["site"]
                    self.new_hosts[new_host].address = n["address"]
                    # transisition to Check_MK 1.1.10p2
                    if 'host_in_downtime' in host:
                        if host['host_in_downtime'] == 'yes':
                            self.new_hosts[new_host].scheduled_downtime = True
                    if 'host_acknowledged' in host:
                        if host['host_acknowledged'] == 'yes':
                            self.new_hosts[new_host].acknowledged = True

        except ZabbixError:
            self.isChecking = False
            result, error = self.Error(sys.exc_info())
            return Result(result=result, error=error)

        # Add filters to the url which should only be applied to the service request
        #if str(self.conf.filter_services_on_unreachable_hosts) == "True":
        #    url_params += '&hst2=0'

        # services
        services = []
        groupids = []
        zabbix_triggers = []
        api_version = self.zapi.api_version()
        try:
            response = []
            try:
                #service = self.zapi.trigger.get({"select_items":"extend","monitored":1,"only_true":1,"min_severity":3,"output":"extend","filter":{}})
                triggers_list = []
                if self.monitor_cgi_url:
                    group_list = self.monitor_cgi_url.split(',')
                    # johncan
                    # self.Debug(str(group_list))
                    hostgroup_ids = [x['groupid'] for x in self.zapi.hostgroup.get(
                        {'output': 'extend',
                         'with_monitored_items': True,
                         'filter': {"name": group_list}}) if int(x['internal']) == 0]
                    # johncan
                    # self.Debug(str(hostgroup_ids))
                    # johncan
                    # hostgroup_ids = [7]
                    zabbix_triggers = self.zapi.trigger.get(
                        {'sortfield': 'lastchange', 'withUnacknowledgedEvents': True, 'groupids': hostgroup_ids,
                         "monitored": True, "filter": {'value': 1}})
                else:
                    zabbix_triggers = self.zapi.trigger.get(
                        {'sortfield': 'lastchange', 'withUnacknowledgedEvents': True, "monitored": True,
                         "filter": {'value': 1}})
                triggers_list = []
                for trigger in zabbix_triggers:
                    triggers_list.append(trigger.get('triggerid'))
                this_trigger = self.zapi.trigger.get(
                    {'triggerids': triggers_list,
                     'expandDescription': True,
                     'output': 'extend',
                     'select_items': 'extend',
                     'expandData': True}
                )
                # johncan
                # self.Debug(str(this_trigger))
                if type(this_trigger) is dict:
                    for triggerid in this_trigger.keys():
                        services.append(this_trigger[triggerid])
                elif type(this_trigger) is list:
                    for trigger in this_trigger:
                        services.append(trigger)

            except ZabbixError, e:
                #print "------------------------------------"
                #print "%s" % e.result.error
                if e.terminate:
                    return e.result
                else:
                    service = e.result.content
                    ret = e.result
            for service in services:
                # johncan
                # self.Debug(str(service))
                if api_version > '1.8':
                    state = '%s' % service['description']
                else:
                    state = '%s=%s' % (service['items'][0]['key_'], service['items'][0]['lastvalue']) 
                n = {
                    'host': service['host'],
                    'service': service['description'],
                    'status': self.statemap.get(service['priority'], service['priority']),
                    'attempt': '0/0',
                    'duration': 60,
                    'status_information': state,
                    'passiveonly': 'no',
                    'last_check': datetime.datetime.fromtimestamp(int(service['lastchange'])),
                    'notifications': 'yes',
                    'flapping': 'no',
                    'site': '',
                    'command': 'zabbix',
                    'triggerid': service['triggerid'],
                }

                nagitems["services"].append(n)
                # after collection data in nagitems create objects of its informations
                # host objects contain service objects
                if n["host"] not in  self.new_hosts:
                    self.new_hosts[n["host"]] = GenericHost()
                    self.new_hosts[n["host"]].name = n["host"]
                    self.new_hosts[n["host"]].status = "UP"
                    self.new_hosts[n["host"]].site = n["site"]
                    self.new_hosts[n["host"]].address = n["host"]
                    # if a service does not exist create its object
                if n["service"] not in  self.new_hosts[n["host"]].services:
                    new_service = n["service"]
                    self.new_hosts[n["host"]].services[new_service] = GenericService()
                    self.new_hosts[n["host"]].services[new_service].host = n["host"]
                    self.new_hosts[n["host"]].services[new_service].name = n["service"]
                    self.new_hosts[n["host"]].services[new_service].status = n["status"]
                    self.new_hosts[n["host"]].services[new_service].last_check = n["last_check"]
                    self.new_hosts[n["host"]].services[new_service].duration = n["duration"]
                    self.new_hosts[n["host"]].services[new_service].attempt = n["attempt"]
                    self.new_hosts[n["host"]].services[new_service].status_information = n["status_information"]
                    self.new_hosts[n["host"]].services[new_service].passiveonly = n["passiveonly"]
                    self.new_hosts[n["host"]].services[new_service].flapping = n["flapping"]
                    self.new_hosts[n["host"]].services[new_service].site = n["site"]
                    self.new_hosts[n["host"]].services[new_service].address = n["host"]
                    self.new_hosts[n["host"]].services[new_service].command = n["command"]
                    self.new_hosts[n["host"]].services[new_service].triggerid = n["triggerid"]

                    if 'svc_in_downtime' in service:
                        if service['svc_in_downtime'] == 'yes':
                            self.new_hosts[n["host"]].services[new_service].scheduled_downtime = True
                    if 'svc_acknowledged' in service:
                        if service['svc_acknowledged'] == 'yes':
                            self.new_hosts[n["host"]].services[new_service].acknowledged = True
                    if 'svc_is_active' in service:
                        if service['svc_is_active'] == 'no':
                            self.new_hosts[n["host"]].services[new_service].passiveonly = True
                    if 'svc_flapping' in service:
                        if service['svc_flapping'] == 'yes':
                            self.new_hosts[n["host"]].services[new_service].flapping = True
        except ZabbixError:
            # set checking flag back to False
            self.isChecking = False
            result, error = self.Error(sys.exc_info())
            print sys.exc_info()
            return Result(result=result, error=error)

        return ret

    def _open_browser(self, url):
        webbrowser.open(url)

        if str(self.conf.debug_mode) == "True":
            self.Debug(server=self.get_name(), debug="Open web page " + url)

    def open_services(self):
        self._open_browser(self.urls['human_services'])

    def open_hosts(self):
        self._open_browser(self.urls['human_hosts'])

    def open_tree_view(self, host, service=""):
        """
        open monitor from treeview context menu
        """

        if service == "":
            url = self.urls['human_host'] + urllib.urlencode(
                {'x': 'site=' + self.hosts[host].site + '&host=' + host}).replace('x=', '%26')
        else:
            url = self.urls['human_service'] + urllib.urlencode(
                {'x': 'site=' + self.hosts[host].site + '&host=' + host + '&service=' + service}).replace('x=', '%26')

        if str(self.conf.debug_mode) == "True":
            self.Debug(server=self.get_name(), host=host, service=service,
                       debug="Open host/service monitor web page " + url)
        webbrowser.open(url)

    def GetHost(self, host):
        """
        find out ip or hostname of given host to access hosts/devices which do not appear in DNS but
        have their ip saved in Nagios
        """

        # the fasted method is taking hostname as used in monitor
        if str(self.conf.connect_by_host) == "True":
            return Result(result=host)

        ip = ""

        try:
            if host in self.hosts:
                ip = self.hosts[host].address

            if str(self.conf.debug_mode) == "True":
                self.Debug(server=self.get_name(), host=host, debug="IP of %s:" % host + " " + ip)

            if str(self.conf.connect_by_dns) == "True":
                try:
                    address = socket.gethostbyaddr(ip)[0]
                except:
                    address = ip
            else:
                address = ip
        except ZabbixError:
            result, error = self.Error(sys.exc_info())
            return Result(result=result, error=error)

        return Result(result=address)

    def _set_recheck(self, host, service):
        pass

    def get_start_end(self, host):
        return time.strftime("%Y-%m-%d %H:%M"), time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 7200))

    def _action(self, site, host, service, specific_params):
        params = {
            'site': self.hosts[host].site,
            'host': host,
        }
        params.update(specific_params)

        if self.zapi is None:
            self._login()
        events = []
        for e in self.zapi.event.get({'triggerids': params['triggerids'],
                                      'hide_unknown': True,
                                      'sortfield': 'clock',
                                      'sortorder': 'desc'}):
            events.append(e['eventid'])
        self.zapi.event.acknowledge({'eventids': events, 'message': params['message']})

    def _set_downtime(self, host, service, author, comment, fixed, start_time, end_time, hours, minutes):
        pass

    def _set_acknowledge(self, host, service, author, comment, sticky, notify, persistent, all_services=[]):
        triggerid = self.hosts[host].services[service].triggerid
        p = {
            'message': '%s: %s' % (author, comment),
            'triggerids': [triggerid],
        }
        self._action(self.hosts[host].site, host, service, p)

        # acknowledge all services on a host when told to do so
        for s in all_services:
            self._action(self.hosts[host].site, host, s, p)
