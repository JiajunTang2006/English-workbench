const fs = require('fs');
const path = require('path');


function loadWorkbenchHtml(options = {}) {
  const root = path.join(__dirname, '..', '..');
  let html = fs.readFileSync(path.join(root, 'workbench.html'), 'utf8');
  if (options.includeThirdParty) {
    const assets = ['xlsx.full.min.js', 'jszip.min.js', 'echarts.min.js']
      .map(filename => `<script src="./workbench-assets/${filename}?v=202608190930"></script>`)
      .join('\n');
    html = html.replace('<script src="./workbench-assets/workbench-core.js', `${assets}\n<script src="./workbench-assets/workbench-core.js`);
  }
  return html.replace(
    /<script src="\.\/workbench-assets\/(workbench-(?:core|views|interactions|growth)\.js)(?:\?[^"]*)"><\/script>/g,
    (_match, filename) => `<script>\n${fs.readFileSync(path.join(root, 'workbench-assets', filename), 'utf8')}\n</script>`,
  );
}


module.exports = { loadWorkbenchHtml };
