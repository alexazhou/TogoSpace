import { describe, it, expect } from 'vitest';
import { mount } from '@vue/test-utils';
import FormField from '../FormField.vue';

describe('FormField', () => {
  it('renders label prop and default slot content', () => {
    const wrapper = mount(FormField, {
      props: { label: 'Model Name' },
      slots: { default: '<input />' },
    });
    expect(wrapper.find('.form-field__label').text()).toBe('Model Name');
    expect(wrapper.find('input').exists()).toBe(true);
  });

  it('renders hint prop', () => {
    const wrapper = mount(FormField, {
      props: { label: 'L', hint: 'Hint text' },
      slots: { default: '<input />' },
    });
    expect(wrapper.find('.form-field__hint').text()).toBe('Hint text');
  });

  it('applies wide modifier class', () => {
    const wrapper = mount(FormField, { props: { wide: true }, slots: { default: '<input />' } });
    expect(wrapper.find('.form-field').classes()).toContain('form-field--wide');
  });

  it('omits wide class by default', () => {
    const wrapper = mount(FormField, { slots: { default: '<input />' } });
    expect(wrapper.find('.form-field').classes()).not.toContain('form-field--wide');
  });

  it('renders #label slot over label prop', () => {
    const wrapper = mount(FormField, {
      props: { label: 'Fallback' },
      slots: { label: 'Custom <b>Label</b>', default: '<input />' },
    });
    expect(wrapper.find('.form-field__label').html()).toContain('Custom');
    expect(wrapper.find('.form-field__label').text()).not.toBe('Fallback');
  });
});