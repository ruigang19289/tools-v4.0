from __future__ import print_function

import json
import time

MODEL_LABELS = {
    "randwrite": "128K 随机写",
    "randread": "128K 随机读",
    "randwrite-4k": "4K 随机写",
    "randread-4k": "4K 随机读",
}


def generate_steady_perf_html(data, output_path):
    with open(output_path, "w") as fh:
        fh.write(build_steady_perf_html(data))


def build_steady_report_data(meta, fill, models, errors=None):
    """One disk's report entry (wrapped by build_steady_report)."""
    return {
        "meta": meta,
        "fill": fill,
        "models": models,
        "errors": errors or [],
    }


def build_steady_report(disk_reports):
    return {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "disks": disk_reports,
    }


def build_steady_perf_html(data):
    payload = json.dumps(data, ensure_ascii=False)
    return r'''<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>稳态性能测试报告</title>
<style>
body { font-family: Arial, sans-serif; margin: 24px; color: #1f2933; background: #f6f8fb; }
h1, h2, h3 { margin: 12px 0; }
.card { background: white; border: 1px solid #d8dee9; border-radius: 8px; padding: 16px; margin: 16px 0; box-shadow: 0 1px 2px rgba(0,0,0,.04); }
table { border-collapse: collapse; width: 100%; background: white; font-size: 13px; }
th, td { border: 1px solid #d8dee9; padding: 6px 8px; text-align: left; }
th { background: #edf2f7; }
.done { color: #0f7b35; font-weight: bold; }
.failed, .error { color: #b00020; font-weight: bold; }
.running { color: #2563eb; font-weight: bold; }
.small { color: #66788a; font-size: 12px; }
.chart { width: 100%; height: 200px; border: 1px solid #d8dee9; background: #fff; margin: 8px 0 18px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.metric { display: block; font-size: 12px; color: #52616b; line-height: 1.5; }
</style>
</head>
<body>
<h1>稳态性能测试报告</h1>
<div id="report"></div>
<script>
var DATA = ''' + payload + r''';
function esc(s){ return String(s == null ? '' : s).replace(/[&<>]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;'}[c];}); }
function fmt(v, d){ if(v == null || isNaN(v)) return '-'; d = d == null ? 2 : d; return (Math.round(v*Math.pow(10,d))/Math.pow(10,d)).toString(); }
function fmtInt(v){ return Math.round(v||0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ','); }
function fmtDur(s){ s = Math.round(s||0); var h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60; return (h?h+'h ':'')+(m?m+'m ':'')+sec+'s'; }
function modelLabel(key){ return MODEL_LABELS[key] || key; }
var MODEL_LABELS = {'randwrite':'128K 随机写','randread':'128K 随机读','randwrite-4k':'4K 随机写','randread-4k':'4K 随机读'};
function statusClass(s){ return {'done':'done','failed':'failed','error':'error','running':'running'}[s] || 'small'; }
function fmtT(v){
  var a = Math.abs(v);
  if(a >= 10000) return (v/1000).toFixed(0)+'k';
  if(a >= 1000) return (v/1000).toFixed(1)+'k';
  if(a >= 100) return Math.round(v).toString();
  if(a >= 10) return (Math.round(v*10)/10).toString();
  return (Math.round(v*100)/100).toString();
}
function addYAxis(svg, w, h, pad, max, ticksY){
  var axis = document.createElementNS('http://www.w3.org/2000/svg','path');
  axis.setAttribute('d','M'+pad+' '+pad+' L'+pad+' '+(h-pad)+' L'+(w-pad)+' '+(h-pad));
  axis.setAttribute('stroke','#334e68'); axis.setAttribute('fill','none'); svg.appendChild(axis);
  for(var i=0;i<=ticksY;i++){
    var v = max*i/ticksY;
    var y = h-pad-(h-pad*2)*i/ticksY;
    var line = document.createElementNS('http://www.w3.org/2000/svg','line');
    line.setAttribute('x1',pad); line.setAttribute('y1',y); line.setAttribute('x2',w-pad); line.setAttribute('y2',y);
    line.setAttribute('stroke','#e2e8f0'); svg.appendChild(line);
    var t = document.createElementNS('http://www.w3.org/2000/svg','text');
    t.setAttribute('x',pad-4); t.setAttribute('y',y+4); t.setAttribute('text-anchor','end');
    t.setAttribute('font-size','10'); t.setAttribute('fill','#52616b'); t.textContent = fmtT(v);
    svg.appendChild(t);
  }
  return function(v){ return h-pad-(h-pad*2)*(v/max); };
}
function drawLine(svg, points, keys, label, unit, maxY){
  var w = svg.clientWidth || 600, h = svg.clientHeight || 200, pad = 44;
  while(svg.firstChild) svg.removeChild(svg.firstChild);
  if(!points || !points.length){ svg.innerHTML='<text x="20" y="40">无数据</text>'; return; }
  if(typeof keys === 'string') keys = [keys];
  var max = maxY||0;
  points.forEach(function(p){ keys.forEach(function(k){ max = Math.max(max, Number(p[k]||0)); }); });
  max = Math.max(max, 1);
  var y = addYAxis(svg, w, h, pad, max, 5);
  var t0 = Number(points[0].time) || 0;
  var tEnd = Number(points[points.length-1].time) || 1;
  function x(i){ return pad + (w-pad*2) * ((Number(points[i].time)-t0)/Math.max(tEnd-t0,1)); }
  var colors = ['#2563eb','#dc2626','#16a34a','#d97706'];
  keys.forEach(function(k, ki){
    var d=''; points.forEach(function(p,i){ var v=Number(p[k]||0); d += (i?' L':'M')+x(i)+' '+y(v); });
    var path=document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d',d); path.setAttribute('stroke',colors[ki%colors.length]); path.setAttribute('fill','none'); path.setAttribute('stroke-width','1.5'); svg.appendChild(path);
  });
  var labelEl=document.createElementNS('http://www.w3.org/2000/svg','text'); labelEl.setAttribute('x',pad); labelEl.setAttribute('y',14); labelEl.textContent=label+' 最大='+fmt(max,0)+(unit||''); svg.appendChild(labelEl);
}
function summaryTable(d){
  var rows = '';
  (d.models||[]).forEach(function(m){
    rows += '<tr><td>'+esc(modelLabel(m.key))+'</td>'+
      '<td class="'+statusClass(m.status)+'">'+esc(m.status)+'</td>'+
      '<td>'+fmt(m.avg_bw_mb_s)+'</td>'+
      '<td>'+fmtInt(m.avg_iops)+'</td>'+
      '<td>'+fmt(m.avg_lat_ms)+'</td>'+
      '<td>'+fmt(m.p99_us)+'</td>'+
      '<td>'+fmt(m.p999_us)+'</td>'+
      '<td>'+fmt(m.p9999_us)+'</td></tr>';
  });
  return '<table><thead><tr><th>模型</th><th>状态</th><th>带宽平均(MB/s)</th><th>IOPS 平均</th><th>时延平均(ms)</th><th>P99(us)</th><th>P999(us)</th><th>P9999(us)</th></tr></thead><tbody>'+rows+'</tbody></table>';
}
function diskInfo(d, di){
  var m = d.meta || {};
  var html = '<div class="card"><h2>'+esc(m.disk||'')+' <span class="small">('+esc(m.node||'')+')</span></h2>';
  html += '<p>型号：'+esc(m.disk_model)+'；容量：'+esc(m.disk_size)+'；开始：'+esc(m.started_at)+'；总耗时：'+fmtDur(m.total_duration_s)+'</p>';
  (d.errors||[]).forEach(function(e){ html += '<p class="error">'+esc(e)+'</p>'; });
  html += summaryTable(d);
  html += '</div>';
  return html;
}
function fillHtml(d, di){
  var f = d.fill || {};
  var html = '<div class="card"><h3>预埋（1M 顺序写满 2 遍）</h3>';
  html += '<p class="small">状态：<span class="'+statusClass(f.status)+'">'+esc(f.status)+'</span>；写满量：'+fmt(f.total_gb)+' GB；平均带宽：'+fmt(f.avg_bw_mb_s)+' MB/s；平均时延：'+fmt(f.avg_lat_ms)+' ms；耗时：'+fmtDur(f.duration_s)+(f.error?'；错误：'+esc(f.error):'')+'</p>';
  html += '<div class="grid"><div><div class="small">带宽曲线(MB/s)</div><svg class="chart" id="d'+di+'-fill-bw"></svg></div><div><div class="small">平均时延曲线(ms)</div><svg class="chart" id="d'+di+'-fill-lat"></svg></div></div>';
  html += '</div>';
  document.getElementById('report').insertAdjacentHTML('beforeend', html);
  drawLine(document.getElementById('d'+di+'-fill-bw'), f.series||[], 'bw_mb_s', '预埋带宽', ' MB/s', 0);
  drawLine(document.getElementById('d'+di+'-fill-lat'), f.series||[], 'lat_ms', '预埋平均时延', ' ms', 0);
}
function modelHtml(d, di, m, mi){
  var html = '<div class="card"><h3>'+esc(modelLabel(m.key))+' <span class="'+statusClass(m.status)+'">'+esc(m.status)+'</span></h3>';
  html += '<div class="grid"><div><div class="small">IOPS 曲线</div><svg class="chart" id="d'+di+'m'+mi+'-iops"></svg></div><div><div class="small">带宽曲线(MB/s)</div><svg class="chart" id="d'+di+'m'+mi+'-bw"></svg></div></div>';
  html += '<div class="grid"><div><div class="small">平均时延曲线(ms)</div><svg class="chart" id="d'+di+'m'+mi+'-lat"></svg></div><div><div class="small">P99/P999/P9999 时延曲线(us)</div><svg class="chart" id="d'+di+'m'+mi+'-pct"></svg></div></div>';
  html += '</div>';
  document.getElementById('report').insertAdjacentHTML('beforeend', html);
  drawLine(document.getElementById('d'+di+'m'+mi+'-iops'), m.series||[], 'iops', 'IOPS', '', 0);
  drawLine(document.getElementById('d'+di+'m'+mi+'-bw'), m.series||[], 'bw_mb_s', '带宽', ' MB/s', 0);
  drawLine(document.getElementById('d'+di+'m'+mi+'-lat'), m.series||[], 'lat_ms', '平均时延', ' ms', 0);
  drawLine(document.getElementById('d'+di+'m'+mi+'-pct'), m.pct_curve||[], ['p99','p999','p9999'], 'P99(蓝)/P999(红)/P9999(绿)', ' us', 0);
}
var top = '<div class="card"><h2>测试信息</h2><p>生成：'+esc(DATA.generated_at)+'；磁盘数：'+((DATA.disks||[]).length)+'</p></div>';
document.getElementById('report').insertAdjacentHTML('beforeend', top);
(DATA.disks||[]).forEach(function(d, di){
  document.getElementById('report').insertAdjacentHTML('beforeend', diskInfo(d, di));
  fillHtml(d, di);
  (d.models||[]).forEach(function(m, mi){ modelHtml(d, di, m, mi); });
});
</script>
</body>
</html>
'''
