import { type AddressInfo, createServer, type Socket } from 'node:net';

import { connectDb } from './db.ts';

it('fails the ping when the database never answers', async () => {
  const sockets: Socket[] = [];
  const server = createServer((socket) => sockets.push(socket));
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const { port } = server.address() as AddressInfo;
  const db = connectDb(`postgres://user:pass@127.0.0.1:${String(port)}/db`, 100);
  try {
    await expect(db.ping()).rejects.toThrow('database ping timed out');
  } finally {
    await db.sql.end({ timeout: 0 });
    for (const socket of sockets) socket.destroy();
    server.close();
  }
});
