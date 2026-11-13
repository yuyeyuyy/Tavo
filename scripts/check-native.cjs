const { spawnSync } = require('node:child_process');

if (process.argv.includes('--electron')) {
  const result = spawnSync(require('electron'), [__filename], {
    stdio: 'inherit', env: { ...process.env, ELECTRON_RUN_AS_NODE: '1' },
  });
  process.exit(result.status ?? 1);
}

// better-sqlite3 13 使用 Node-API 预构建；实际加载与读写比空重编译更能证明兼容性。
const Database = require('better-sqlite3');
const db = new Database(':memory:');
try {
  db.exec('CREATE TABLE native_check (value TEXT NOT NULL)');
  db.prepare('INSERT INTO native_check VALUES (?)').run('tavo');
  if (db.prepare('SELECT value FROM native_check').get().value !== 'tavo') throw new Error('SQLite 读写检查失败');
  console.log(JSON.stringify({ runtime: process.versions.electron ? 'Electron' : 'Node', node: process.versions.node, electron: process.versions.electron, napi: process.versions.napi, sqlite: db.prepare('SELECT sqlite_version() AS version').get().version, status: 'SQLite 加载与读写通过' }));
} finally { db.close(); }
