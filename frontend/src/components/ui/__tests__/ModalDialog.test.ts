import { describe, it, expect, afterEach } from 'vitest';
import { mount } from '@vue/test-utils';
import ModalDialog from '../ModalDialog.vue';

function queryOverlay(): HTMLElement | null {
  return document.body.querySelector('.ui-modal__overlay');
}

describe('ModalDialog', () => {
  afterEach(() => {
    document.body.innerHTML = '';
  });

  it('renders nothing when closed', () => {
    mount(ModalDialog, { props: { open: false } });
    expect(queryOverlay()).toBeNull();
  });

  it('renders overlay and dialog when open', () => {
    mount(ModalDialog, { props: { open: true } });
    expect(queryOverlay()).not.toBeNull();
    expect(queryOverlay()!.querySelector('.ui-modal__dialog')).not.toBeNull();
  });

  it('applies width and z-index via style', () => {
    mount(ModalDialog, { props: { open: true, width: 600, zIndex: 90 } });
    const dialog = queryOverlay()!.querySelector('.ui-modal__dialog') as HTMLElement;
    expect(dialog.style.getPropertyValue('--modal-width')).toBe('600px');
    expect(queryOverlay()!.style.zIndex).toBe('90');
  });

  it('renders title and eyebrow in head', () => {
    mount(ModalDialog, { props: { open: true, title: 'Model Editor', eyebrow: 'MODEL' } });
    const headText = queryOverlay()!.querySelector('.ui-modal__head-text')!;
    expect(headText.textContent).toContain('MODEL');
    expect(headText.textContent).toContain('Model Editor');
  });

  it('renders default slot content', () => {
    mount(ModalDialog, { props: { open: true }, slots: { default: 'Body Content' } });
    expect(queryOverlay()!.textContent).toContain('Body Content');
  });

  it('renders footer slots', () => {
    mount(ModalDialog, {
      props: { open: true },
      slots: {
        'footer-leading': '<button>Reset</button>',
        'footer-trailing': '<button>Save</button>',
        footer: '<button>Raw</button>',
      },
    });
    const text = queryOverlay()!.textContent || '';
    expect(text).toContain('Reset');
    expect(text).toContain('Save');
    expect(text).toContain('Raw');
  });

  it('emits close on close button click', async () => {
    const wrapper = mount(ModalDialog, { props: { open: true } });
    const btn = queryOverlay()!.querySelector('.ui-modal__close') as HTMLElement;
    await btn.click();
    expect(wrapper.emitted('close')).toBeTruthy();
  });

  it('emits close on overlay self click', async () => {
    const wrapper = mount(ModalDialog, { props: { open: true } });
    queryOverlay()!.click();
    expect(wrapper.emitted('close')).toBeTruthy();
  });

  it('does not emit close when clicking dialog content', async () => {
    const wrapper = mount(ModalDialog, {
      props: { open: true },
      slots: { default: '<div class="inner-content">Body</div>' },
    });
    const inner = queryOverlay()!.querySelector('.inner-content') as HTMLElement;
    await inner.click();
    expect(wrapper.emitted('close')).toBeFalsy();
  });
});