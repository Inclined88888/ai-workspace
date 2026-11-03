import { groupHits, groupLinks, Hit } from '../knowledge';

const h = (kind: string, score: number): Hit => ({ kind, ref: '1', title: kind, project_id: null, at: '', score });

test('search hits group by kind, best group first', () => {
  const g = groupHits([h('task', 0.2), h('message', 0.9), h('task', 0.5), h('weird', 0.1)]);
  expect(g.map(([k, xs]) => [k, xs.length])).toEqual([['Chats', 1], ['Tasks', 2], ['Other', 1]]);
});

test('entity links group in a friendly order and drop empty groups', () => {
  const g = groupLinks([{ id: 1, type: 'task', name: 'a', rel: 'part_of' }, { id: 2, type: 'person', name: 'b', rel: 'member_of' }]);
  expect(g.map(([k]) => k)).toEqual(['People', 'Tasks']);
});
