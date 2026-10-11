"""Actual Chromium checks for the generated dependency-free projected 3D artifact."""

import shutil

import pytest

from jarvis.extensions.creative import write_website


@pytest.fixture(scope='module')
def chromium():
    playwright = pytest.importorskip('playwright.sync_api')
    executable = shutil.which('chromium') or shutil.which('chromium-browser')
    if not executable:
        pytest.skip('System Chromium is required for website rendering checks')
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True, executable_path=executable)
        yield browser
        browser.close()


@pytest.fixture
def artifact(tmp_path):
    destination = tmp_path / 'business.html'
    write_website(destination, title='<script>window.injected=true</script>',
                  description='<img src="https://example.com/track" onerror="window.injected=true">',
                  metrics={'<img src=x>': '</strong><script>window.injected=true</script>'})
    return destination


def canvas_image(page):
    return page.locator('#scene-canvas').evaluate('(canvas) => canvas.toDataURL()')


def test_website_default_contains_native_geometry_and_no_external_assets(artifact):
    document = artifact.read_text()
    assert '<canvas' in document
    assert 'getContext' in document and 'perspective' in document
    assert '&lt;script&gt;window.injected=true&lt;/script&gt;' in document
    assert 'https://cdn.' not in document
    assert 'WebGLRenderer' not in document
    for identifier in ('motion', 'reset-view', 'rotate-left', 'rotate-right', 'rotate-up', 'rotate-down'):
        assert f'id="{identifier}"' in document


def test_chromium_canvas_pointer_keyboard_reset_and_no_network(chromium, artifact):
    context = chromium.new_context(viewport={'width': 1000, 'height': 900}, reduced_motion='reduce')
    page = context.new_page()
    requests, errors = [], []
    context.route('http://**/*', lambda route: route.abort())
    context.route('https://**/*', lambda route: route.abort())
    page.on('request', lambda request: requests.append(request.url))
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.set_content(artifact.read_text())
        assert page.locator('#scene-canvas').count() == 1
        assert page.locator('#motion').inner_text() == 'Resume motion'
        assert page.evaluate('window.injected') is None
        assert page.locator('main img, main script').count() == 0
        nonempty = page.locator('#scene-canvas').evaluate('''canvas => {
            const data=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
            let visible=0, green=0;
            for(let i=0;i<data.length;i+=4){if(data[i+3])visible++;
                if(data[i+1]>data[i]+20 && data[i+1]>data[i+2]-30)green++;}
            return {visible,green};
        }''')
        assert nonempty['visible'] > 1000 and nonempty['green'] > 100
        initial = canvas_image(page)
        page.wait_for_timeout(80)
        assert canvas_image(page) == initial
        canvas = page.locator('#scene-canvas')
        box = canvas.bounding_box()
        page.mouse.move(box['x'] + 120, box['y'] + 100)
        page.mouse.down()
        page.mouse.move(box['x'] + 200, box['y'] + 140, steps=5)
        page.mouse.up()
        assert canvas_image(page) != initial
        page.get_by_role('button', name='Reset view').click()
        assert canvas_image(page) == initial
        canvas.focus()
        page.keyboard.press('ArrowRight')
        assert canvas_image(page) != initial
        page.get_by_role('button', name='Reset view').focus()
        page.keyboard.press('Enter')
        assert canvas_image(page) == initial
        page.get_by_role('button', name='Rotate left').focus()
        page.keyboard.press('Space')
        assert canvas_image(page) != initial
        assert errors == []
        assert not [url for url in requests if url.startswith(('http:', 'https:'))]
        page.screenshot(path=str(artifact.parent / 'canvas-3d-chromium.png'), full_page=True)
    finally:
        context.close()


def test_chromium_motion_pause_and_mobile_layout(chromium, artifact):
    context = chromium.new_context(viewport={'width': 320, 'height': 900}, reduced_motion='no-preference')
    page = context.new_page()
    try:
        page.set_content(artifact.read_text())
        assert page.locator('#scene-canvas').count() == 1
        initial = canvas_image(page)
        page.wait_for_timeout(100)
        assert canvas_image(page) != initial
        page.get_by_role('button', name='Pause motion').click()
        paused = canvas_image(page)
        page.wait_for_timeout(100)
        assert canvas_image(page) == paused
        assert page.locator('#motion').get_attribute('aria-pressed') == 'true'
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.get_by_role('button', name='Resume motion').click()
        page.emulate_media(reduced_motion='reduce')
        page.get_by_role('button', name='Resume motion').wait_for()
        reduced = canvas_image(page)
        page.wait_for_timeout(100)
        assert canvas_image(page) == reduced
    finally:
        context.close()


def test_chromium_explicit_cdn_failure_preserves_native_controls(chromium, tmp_path):
    destination = tmp_path / 'cdn.html'
    write_website(destination, title='Shop', description='Supplied business facts', metrics={}, allow_cdn=True)
    context = chromium.new_context(reduced_motion='reduce')
    page = context.new_page()
    requests = []
    def block(route):
        requests.append(route.request.url)
        route.abort()
    context.route('https://**/*', block)
    try:
        page.set_content(destination.read_text())
        assert page.locator('#scene-canvas').count() == 1
        page.locator('.notice').filter(has_text='native').wait_for()
        initial = canvas_image(page)
        page.get_by_role('button', name='Rotate right').click()
        assert canvas_image(page) != initial
        assert requests == ['https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js']
    finally:
        context.close()
