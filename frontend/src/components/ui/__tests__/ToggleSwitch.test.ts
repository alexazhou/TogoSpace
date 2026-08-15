import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import ToggleSwitch from '../ToggleSwitch.vue';

describe('ToggleSwitch', () => {
  it('renders with checked state', () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: true } });
    expect(wrapper.find('.ui-toggle').classes()).toContain('is-checked');
  });

  it('renders unchecked state', () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: false } });
    expect(wrapper.find('.ui-toggle').classes()).not.toContain('is-checked');
  });

  it('emits toggle event on click', async () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: false } });
    await wrapper.trigger('click');
    const emitted = wrapper.emitted('toggle');
    expect(emitted).toBeTruthy();
    expect(emitted![0]).toEqual([true]);
  });

  it('emits false when already checked', async () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: true } });
    await wrapper.trigger('click');
    const emitted = wrapper.emitted('toggle');
    expect(emitted).toBeTruthy();
    expect(emitted![0]).toEqual([false]);
  });

  it('does not emit when disabled', async () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: false, disabled: true } });
    await wrapper.trigger('click');
    expect(wrapper.emitted('toggle')).toBeFalsy();
  });

  it('renders label when provided', () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: false, label: 'Test Label' } });
    expect(wrapper.find('.ui-toggle__label').text()).toBe('Test Label');
  });

  it('applies variant class', () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: false, variant: 'inline' } });
    expect(wrapper.find('.ui-toggle').classes()).toContain('ui-toggle--inline');
  });

  it('applies size class', () => {
    const wrapper = mount(ToggleSwitch, { props: { checked: false, size: 'sm' } });
    expect(wrapper.find('.ui-toggle').classes()).toContain('ui-toggle--sm');
  });
});