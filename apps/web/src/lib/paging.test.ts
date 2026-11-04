import {
  DEFAULT_SIZE,
  keepFirstRow,
  MAX_OFFSET,
  offsetOf,
  pageCount,
  pageHref,
  pageWindow,
  paging,
  SIZES,
  storedSize,
  storeSize,
} from './paging.ts';

describe('paging', () => {
  it('reads the page and size from the URL, else page 1 at 20 a page', () => {
    expect(SIZES).toEqual([10, 20, 50]);
    expect(DEFAULT_SIZE).toBe(20);
    expect(paging({ page: '3', size: '50' })).toEqual({ page: 3, size: 50 });
    expect(paging({})).toEqual({ page: 1, size: 20 });
    for (const page of ['0', '-2', '2.5', 'two', '', '1e3', '99999999999999999999', ['2', '3']]) {
      expect([page, paging({ page }).page]).toEqual([page, 1]);
    }
    for (const size of ['15', '0', 'all', '', ['10', '50']]) {
      expect([size, paging({ size }).size]).toEqual([size, 20]);
    }
  });

  it('skips the earlier pages, never past the gateway limit', () => {
    expect(offsetOf(1, 20)).toBe(0);
    expect(offsetOf(3, 50)).toBe(100);
    expect(MAX_OFFSET).toBe(1_000_000);
    expect(offsetOf(9_000_000, 50)).toBe(MAX_OFFSET);
  });

  it('counts the pages, one at least so an empty list still has page 1', () => {
    expect([pageCount(0, 20), pageCount(20, 20), pageCount(21, 20), pageCount(65, 10)]).toEqual([
      1, 1, 2, 7,
    ]);
  });

  it('shows every page up to seven, else the first, the last and the current with gaps', () => {
    expect(pageWindow(1, 1)).toEqual([1]);
    expect(pageWindow(4, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
    expect(pageWindow(1, 20)).toEqual([1, 2, 3, 4, 5, null, 20]);
    expect(pageWindow(4, 20)).toEqual([1, 2, 3, 4, 5, null, 20]);
    expect(pageWindow(5, 20)).toEqual([1, null, 4, 5, 6, null, 20]);
    expect(pageWindow(17, 20)).toEqual([1, null, 16, 17, 18, 19, 20]);
    expect(pageWindow(20, 20)).toEqual([1, null, 16, 17, 18, 19, 20]);
  });

  it('keeps the first row of the page in view when the size changes', () => {
    // Rows 21 to 40, then 50 a page: page 1 holds row 21.
    expect(keepFirstRow(2, 20, 50)).toBe(1);
    // Rows 101 to 150, then 10 a page: page 11 starts at row 101.
    expect(keepFirstRow(3, 50, 10)).toBe(11);
    // Rows 41 to 50, then 20 a page: page 3 holds rows 41 to 60.
    expect(keepFirstRow(5, 10, 20)).toBe(3);
  });

  it("links a page and size, keeping the URL's other settings and dropping an old cursor", () => {
    expect(pageHref('/investigations', 'source=sim&cursor=abc', 2, 50)).toBe(
      '/investigations?source=sim&page=2&size=50',
    );
    expect(pageHref('/demo', new URLSearchParams('offline=1&page=4&size=10'), 1, 20)).toBe(
      '/demo?offline=1&page=1&size=20',
    );
  });

  describe('the remembered size', () => {
    const saved = new Map<string, string>();
    const local = {
      getItem: (key: string) => saved.get(key) ?? null,
      setItem: (key: string, value: string) => saved.set(key, value),
    };
    afterEach(() => {
      saved.clear();
      Reflect.deleteProperty(globalThis, 'window');
    });

    it('keeps the size in localStorage, and reads back only a size it offers', () => {
      Object.assign(globalThis, { window: { localStorage: local } });
      expect(storedSize()).toBeUndefined();
      storeSize(50);
      expect([saved.get('tc-page-size'), storedSize()]).toEqual(['50', 50]);
      saved.set('tc-page-size', '1000');
      expect(storedSize()).toBeUndefined();
    });

    it('carries on without it where storage is blocked or missing', () => {
      const blocked = () => {
        throw new Error('SecurityError: storage is blocked');
      };
      Object.assign(globalThis, {
        window: { localStorage: { getItem: blocked, setItem: blocked } },
      });
      expect(storedSize()).toBeUndefined();
      expect(() => {
        storeSize(10);
      }).not.toThrow();
      Reflect.deleteProperty(globalThis, 'window');
      expect(storedSize()).toBeUndefined();
    });
  });
});
