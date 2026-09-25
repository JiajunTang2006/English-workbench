const fs = require('fs');
const path = require('path');

try {
  for (const filename of ['workbench-core.js', 'workbench-views.js', 'workbench-interactions.js']) {
    const source = fs.readFileSync(path.join(__dirname, '..', 'workbench-assets', filename), 'utf8');
    new Function(source);
  }
  console.log('JavaScript syntax check passed.');
} catch (error) {
  console.error('JavaScript syntax check failed:', error.message);
  process.exit(1);
}
