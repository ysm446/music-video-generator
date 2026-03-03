"""フェーズ2 動作確認テスト（日本語出力なし）"""
import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, 'd:/GitHub/music-video-generator')
os.chdir('d:/GitHub/music-video-generator')

import api
from fastapi.testclient import TestClient
client = TestClient(api.app)

# scenes endpoint
r = client.get('/api/projects/Gymnopedie/scenes')
print('GET /scenes:', r.status_code)
scenes = r.json()['scenes']
print('scene_count:', len(scenes))
print('first scene keys:', sorted(scenes[0].keys()) if scenes else [])

# single scene
r2 = client.get('/api/projects/Gymnopedie/scenes/1')
print('GET /scenes/1:', r2.status_code)

# save scene
r3 = client.put('/api/projects/Gymnopedie/scenes/1', json={'notes': 'test note'})
print('PUT /scenes/1:', r3.status_code)

# bulk-save
r4 = client.post('/api/projects/Gymnopedie/scenes/bulk-save',
                 json={'rows': [], 'concept': 'test'})
print('bulk-save:', r4.status_code, r4.json().get('updated'))

# llm import check (no actual call)
from api_routes.llm import router as llm_router
print('LLM router routes:', [r.path for r in llm_router.routes])

print('ALL OK')
