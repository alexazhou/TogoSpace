import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import UiTag from '../UiTag.vue';

describe('UiTag', () => {
  it('renders slot content', () => {
    const wrapper = mount(UiTag, { slots: { default: 'Vision' } });
    expect(wrapper.find('.ui-tag').text()).toBe('Vision');
  });

  it('applies default tone/size/shape classes', () => {
    const wrapper = mount(UiTag, { slots: { default: 'text' } });
    expect(wrapper.find('.ui-tag').classes()).toEqual(
      expect.arrayContaining([
        'ui-tag--tone-default',
        'ui-tag--size-md',
        'ui-tag--shape-pill',
      ]),
    );
  });

  it('applies tone class', () => {
    const wrapper = mount(UiTag, { props: { tone: 'success' }, slots: { default: 'x' } });
    expect(wrapper.find('.ui-tag').classes()).toContain('ui-tag--tone-success');
  });

  it('applies size class', () => {
    const wrapper = mount(UiTag, { props: { size: 'xs' }, slots: { default: 'x' } });
    expect(wrapper.find('.ui-tag').classes()).toContain('ui-tag--size-xs');
  });

  it('applies shape class', () => {
    const wrapper = mount(UiTag, { props: { shape: 'rounded' }, slots: { default: 'x' } });
    expect(wrapper.find('.ui-tag').classes()).toContain('ui-tag--shape-rounded');
  });

  it('inherits external class onto root element', () => {
    const wrapper = mount(UiTag, { attrs: { class: 'extra-tag' }, slots: { default: 'x' } });
    expect(wrapper.find('.ui-tag').classes()).toContain('extra-tag');
  });
});