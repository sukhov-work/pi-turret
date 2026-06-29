#!/usr/bin/env python3
"""Generate the two pi-turret Grafana dashboards as importable JSON.

Run:  python3 monitoring/dashboards/generate_dashboards.py
Emits (next to this script):
  - pi-health.json        Raspberry Pi system health (node_exporter)
  - turret-telemetry.json Turret liveness + log-derived metrics + Loki log panels

Import in Grafana: Dashboards -> New -> Import -> Upload JSON. Grafana detects the
``__inputs`` datasource placeholders and prompts you to pick your Grafana Cloud
Prometheus and Loki data sources. No UIDs are hard-coded, so it works on any stack.

All metric queries are scoped to instance="pi-jayson" and match config.alloy's labels.
"""
import json
import os

INSTANCE = 'pi-jayson'
PROM = '${DS_PROM}'
LOKI = '${DS_LOKI}'


def prom_ds():
    return {'type': 'prometheus', 'uid': PROM}


def loki_ds():
    return {'type': 'loki', 'uid': LOKI}


def target(expr, legend='', instant=False):
    return {
        'datasource': prom_ds(),
        'expr': expr,
        'legendFormat': legend,
        'range': not instant,
        'instant': instant,
        'refId': 'A',
    }


def logql(expr):
    return {'datasource': loki_ds(), 'expr': expr, 'queryType': 'range', 'refId': 'A'}


def gridpos(x, y, w, h):
    return {'h': h, 'w': w, 'x': x, 'y': y}


def timeseries(title, exprs, gp, unit='short', legends=None, fill=10, stack=False):
    legends = legends or [''] * len(exprs)
    targets = []
    for i, (e, lg) in enumerate(zip(exprs, legends)):
        t = target(e, lg)
        t['refId'] = chr(ord('A') + i)
        targets.append(t)
    return {
        'type': 'timeseries', 'title': title, 'datasource': prom_ds(),
        'gridPos': gp, 'targets': targets,
        'fieldConfig': {
            'defaults': {
                'unit': unit, 'custom': {
                    'drawStyle': 'line', 'lineWidth': 1, 'fillOpacity': fill,
                    'showPoints': 'never', 'spanNulls': True,
                    'stacking': {'mode': 'normal' if stack else 'none', 'group': 'A'},
                },
            }, 'overrides': [],
        },
        'options': {'legend': {'displayMode': 'list', 'placement': 'bottom', 'calcs': ['lastNotNull', 'max']},
                    'tooltip': {'mode': 'multi', 'sort': 'desc'}},
    }


def stat(title, expr, gp, unit='short', mappings=None, thresholds=None, color_mode='value',
         graph=False, instant=True):
    defaults = {'unit': unit, 'mappings': mappings or []}
    if thresholds:
        defaults['thresholds'] = {'mode': 'absolute', 'steps': thresholds}
        defaults['color'] = {'mode': 'thresholds'}
    return {
        'type': 'stat', 'title': title, 'datasource': prom_ds(), 'gridPos': gp,
        'targets': [target(expr, instant=instant)],
        'fieldConfig': {'defaults': defaults, 'overrides': []},
        'options': {'colorMode': color_mode, 'graphMode': 'area' if graph else 'none',
                    'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                    'textMode': 'auto'},
    }


def gauge(title, expr, gp, unit='percent', thresholds=None):
    return {
        'type': 'gauge', 'title': title, 'datasource': prom_ds(), 'gridPos': gp,
        'targets': [target(expr, instant=True)],
        'fieldConfig': {'defaults': {
            'unit': unit, 'min': 0, 'max': 100,
            'thresholds': {'mode': 'absolute', 'steps': thresholds or [
                {'color': 'green', 'value': None}, {'color': 'yellow', 'value': 70},
                {'color': 'red', 'value': 90}]},
            'color': {'mode': 'thresholds'}}, 'overrides': []},
        'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                    'showThresholdLabels': False, 'showThresholdMarkers': True},
    }


def heatmap(title, expr, gp):
    return {
        'type': 'heatmap', 'title': title, 'datasource': prom_ds(), 'gridPos': gp,
        'targets': [{'datasource': prom_ds(), 'expr': expr, 'format': 'heatmap',
                     'legendFormat': '{{le}}', 'range': True, 'refId': 'A'}],
        'options': {'calculate': False, 'cellGap': 1,
                    'color': {'scheme': 'Oranges', 'mode': 'scheme', 'steps': 64},
                    'yAxis': {'unit': 'short'}},
    }


def logs(title, expr, gp):
    return {
        'type': 'logs', 'title': title, 'datasource': loki_ds(), 'gridPos': gp,
        'targets': [logql(expr)],
        'options': {'showTime': True, 'wrapLogMessage': True, 'sortOrder': 'Descending',
                    'enableLogDetails': True, 'dedupStrategy': 'none'},
    }


def row(title, y):
    return {'type': 'row', 'title': title, 'collapsed': False,
            'gridPos': gridpos(0, y, 24, 1), 'panels': []}


def dashboard(title, uid, tags, panels):
    return {
        '__inputs': [
            {'name': 'DS_PROM', 'label': 'Prometheus', 'description': 'Grafana Cloud Prometheus',
             'type': 'datasource', 'pluginId': 'prometheus', 'pluginName': 'Prometheus'},
            {'name': 'DS_LOKI', 'label': 'Loki', 'description': 'Grafana Cloud Loki',
             'type': 'datasource', 'pluginId': 'loki', 'pluginName': 'Loki'},
        ],
        '__requires': [
            {'type': 'grafana', 'id': 'grafana', 'name': 'Grafana', 'version': '10.0.0'},
            {'type': 'datasource', 'id': 'prometheus', 'name': 'Prometheus', 'version': '1.0.0'},
            {'type': 'datasource', 'id': 'loki', 'name': 'Loki', 'version': '1.0.0'},
        ],
        'annotations': {'list': [{'builtIn': 1, 'datasource': {'type': 'grafana', 'uid': '-- Grafana --'},
                                  'enable': True, 'hide': True, 'iconColor': 'rgba(0, 211, 255, 1)',
                                  'name': 'Annotations & Alerts', 'type': 'dashboard'}]},
        'editable': True, 'fiscalYearStartMonth': 0, 'graphTooltip': 1, 'links': [],
        'liveNow': False, 'panels': panels, 'refresh': '30s', 'schemaVersion': 39,
        'style': 'dark', 'tags': tags, 'templating': {'list': []},
        'time': {'from': 'now-6h', 'to': 'now'}, 'timepicker': {},
        'timezone': '', 'title': title, 'uid': uid, 'version': 1, 'weekStart': '',
    }


def i(metric, extra=''):
    """Helper: metric{instance="pi-jayson"<,extra>}"""
    sel = 'instance="%s"' % INSTANCE
    if extra:
        sel += ',' + extra
    return '%s{%s}' % (metric, sel)


def build_pi_health():
    p = []
    y = 0
    p.append(row('Overview', y)); y += 1
    p.append(stat('Uptime', '%s - %s' % (i('node_time_seconds'), i('node_boot_time_seconds')),
                  gridpos(0, y, 4, 4), unit='s'))
    p.append(stat('CPU temperature', 'max(%s)' % i('node_hwmon_temp_celsius'),
                  gridpos(4, y, 4, 4), unit='celsius', color_mode='background', graph=True,
                  thresholds=[{'color': 'green', 'value': None}, {'color': 'yellow', 'value': 65},
                              {'color': 'orange', 'value': 75}, {'color': 'red', 'value': 82}]))
    p.append(gauge('Root disk used', '100 - (%s * 100 / %s)' % (
        i('node_filesystem_avail_bytes', 'mountpoint="/"'),
        i('node_filesystem_size_bytes', 'mountpoint="/"')), gridpos(8, y, 4, 4)))
    p.append(stat('systemd failed units',
                  'count(%s == 1) or vector(0)' % i('node_systemd_unit_state', 'state="failed"'),
                  gridpos(12, y, 4, 4), color_mode='background',
                  thresholds=[{'color': 'green', 'value': None}, {'color': 'red', 'value': 1}]))
    p.append(stat('Memory used',
                  '(%s - %s)' % (i('node_memory_MemTotal_bytes'), i('node_memory_MemAvailable_bytes')),
                  gridpos(16, y, 4, 4), unit='bytes', graph=True))
    p.append(stat('CPU busy',
                  '100 - (avg(rate(%s[$__rate_interval])) * 100)' % i('node_cpu_seconds_total', 'mode="idle"'),
                  gridpos(20, y, 4, 4), unit='percent', graph=True, color_mode='background',
                  thresholds=[{'color': 'green', 'value': None}, {'color': 'yellow', 'value': 70},
                              {'color': 'red', 'value': 90}]))
    y += 4
    p.append(row('CPU & Load', y)); y += 1
    p.append(timeseries('CPU busy % per core',
                        ['100 - (avg by (cpu)(rate(%s[$__rate_interval])) * 100)' % i('node_cpu_seconds_total', 'mode="idle"')],
                        gridpos(0, y, 12, 8), unit='percent', legends=['core {{cpu}}']))
    p.append(timeseries('Load average', [i('node_load1'), i('node_load5'), i('node_load15')],
                        gridpos(12, y, 12, 8), legends=['1m', '5m', '15m'], fill=0))
    y += 8
    p.append(row('Memory & Disk', y)); y += 1
    p.append(timeseries('Memory',
                        [i('node_memory_MemTotal_bytes'),
                         '%s - %s' % (i('node_memory_MemTotal_bytes'), i('node_memory_MemAvailable_bytes')),
                         '%s - %s' % (i('node_memory_SwapTotal_bytes'), i('node_memory_SwapFree_bytes'))],
                        gridpos(0, y, 12, 8), unit='bytes', legends=['total', 'used', 'swap used']))
    p.append(timeseries('Disk I/O (SD card)',
                        ['rate(%s[$__rate_interval])' % i('node_disk_read_bytes_total'),
                         'rate(%s[$__rate_interval])' % i('node_disk_written_bytes_total')],
                        gridpos(12, y, 12, 8), unit='Bps', legends=['{{device}} read', '{{device}} write']))
    y += 8
    p.append(row('Network', y)); y += 1
    p.append(timeseries('Network throughput',
                        ['rate(%s[$__rate_interval])' % i('node_network_receive_bytes_total', 'device!~"lo|veth.*|docker.*"'),
                         'rate(%s[$__rate_interval])' % i('node_network_transmit_bytes_total', 'device!~"lo|veth.*|docker.*"')],
                        gridpos(0, y, 24, 8), unit='Bps', legends=['{{device}} rx', '{{device}} tx']))
    y += 8
    return dashboard('pi-turret / Raspberry Pi Health', 'piturret-pi-health',
                     ['pi-turret', 'raspberrypi'], p)


def build_turret():
    p = []
    y = 0
    up_map = [{'type': 'value', 'options': {'0': {'text': 'DOWN', 'color': 'red'},
                                            '1': {'text': 'UP', 'color': 'green'}}}]
    p.append(row('Liveness', y)); y += 1
    p.append(stat('turret.service',
                  '%s' % i('node_systemd_unit_state', 'name="turret.service",state="active"'),
                  gridpos(0, y, 4, 4), mappings=up_map, color_mode='background',
                  thresholds=[{'color': 'red', 'value': None}, {'color': 'green', 'value': 1}]))
    p.append(stat('alloy.service',
                  '%s' % i('node_systemd_unit_state', 'name="alloy.service",state="active"'),
                  gridpos(4, y, 4, 4), mappings=up_map, color_mode='background',
                  thresholds=[{'color': 'red', 'value': None}, {'color': 'green', 'value': 1}]))
    p.append(stat('turret restarts (1h)',
                  'changes(%s[1h])' % i('node_systemd_unit_start_time_seconds', 'name="turret.service"'),
                  gridpos(8, y, 4, 4), color_mode='background',
                  thresholds=[{'color': 'green', 'value': None}, {'color': 'yellow', 'value': 1},
                              {'color': 'red', 'value': 3}]))
    p.append(stat('Total shots ($__range)', 'sum(increase(turret_fire_events_total[$__range]))',
                  gridpos(12, y, 6, 4), graph=True, color_mode='value'))
    p.append(stat('Alloy build', 'alloy_build_info{instance="%s"}' % INSTANCE,
                  gridpos(18, y, 6, 4), color_mode='none'))
    y += 4
    p.append(row('Fire & Aim', y)); y += 1
    p.append(timeseries('Fire rate', ['sum(rate(turret_fire_events_total[$__rate_interval]))'],
                        gridpos(0, y, 12, 8), unit='cps', legends=['fires/s']))
    p.append(timeseries('Aim error at fire', ['turret_aim_error_px{instance="%s"}' % INSTANCE],
                        gridpos(12, y, 12, 8), unit='none', legends=['aim_err px'], fill=0))
    y += 8
    p.append(heatmap('Aim error distribution (px)',
                     'sum(rate(turret_aim_error_px_hist_bucket{instance="%s"}[$__rate_interval])) by (le)' % INSTANCE,
                     gridpos(0, y, 12, 8)))
    p.append(timeseries('State transitions',
                        ['sum by (to_state)(increase(turret_state_transitions_total[$__rate_interval]))'],
                        gridpos(12, y, 12, 8), legends=['{{to_state}}'], stack=True))
    y += 8
    p.append(timeseries('Target events',
                        ['sum by (event)(rate(turret_target_events_total[$__rate_interval]))'],
                        gridpos(0, y, 24, 6), unit='cps', legends=['{{event}}']))
    y += 6
    p.append(row('Logs (Loki)', y)); y += 1
    p.append(logs('FIRE events',
                  '{job="integrations/raspberrypi-node", instance="%s"} |= "FIRE"' % INSTANCE,
                  gridpos(0, y, 12, 9)))
    p.append(logs('turret.service log', '{unit="turret.service"}', gridpos(12, y, 12, 9)))
    y += 9
    p.append(logs('Errors & warnings',
                  '{job="integrations/raspberrypi-node", instance="%s"} |~ "(?i)(error|warn|traceback|exception)"' % INSTANCE,
                  gridpos(0, y, 24, 8)))
    y += 8
    return dashboard('pi-turret / Turret Telemetry', 'piturret-turret', ['pi-turret', 'turret'], p)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    for name, d in [('pi-health.json', build_pi_health()),
                    ('turret-telemetry.json', build_turret())]:
        path = os.path.join(here, name)
        with open(path, 'w') as f:
            json.dump(d, f, indent=2)
            f.write('\n')
        print('wrote', path, '(%d panels)' % len([x for x in d['panels'] if x['type'] != 'row']))


if __name__ == '__main__':
    main()
