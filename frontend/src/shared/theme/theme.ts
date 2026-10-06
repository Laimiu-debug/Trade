import { theme, type ThemeConfig } from 'antd'
import { designTokens } from './design-tokens.generated'

export function workspaceTheme(mode: 'light' | 'dark'): ThemeConfig {
 const colors = designTokens.themes[mode].color
 return {
  algorithm: mode === 'dark' ? theme.darkAlgorithm : theme.defaultAlgorithm,
  token: {
    colorPrimary: colors['action.primary'], colorInfo: colors['status.info'],
    colorSuccess: colors['status.success'], colorWarning: colors['status.warning'], colorError: colors['status.danger'],
    colorBgBase: colors['bg.surface'], colorBgLayout: colors['bg.canvas'], colorBgContainer: colors['bg.surface'],
    colorText: colors['text.primary'], colorTextSecondary: colors['text.secondary'], colorTextTertiary: colors['text.muted'],
    colorBorder: colors['border.control'], colorBorderSecondary: colors['border.subtle'],
    colorTextLightSolid: colors['text.onPrimary'],
    borderRadius: designTokens.radiusPx.control, fontFamily: designTokens.font.family.ui,
    fontSize: designTokens.font.sizePx.body, controlHeight: designTokens.sizePx.control,
    boxShadowSecondary: designTokens.themes[mode].shadow.overlay,
  },
  components: {
    Layout: {
      siderBg: colors['nav.background'],
      bodyBg: colors['bg.canvas'],
      headerBg: colors['bg.surface'],
    },
    Card: {
      borderRadiusLG: designTokens.radiusPx.card, bodyPadding: designTokens.sizePx.cardPadding, headerFontSize: 16,
    },
    Menu: {
      itemBorderRadius: designTokens.radiusPx.control,
      itemSelectedBg: colors['nav.active'],
      itemSelectedColor: colors['action.primary'],
      itemColor: colors['nav.text'], itemHeight: 40, itemMarginBlock: 3,
    },
    Button: {
      defaultBorderColor: colors['border.subtle'], fontWeight: 500, primaryShadow: 'none', primaryColor: colors['text.onPrimary'],
    },
    Alert: {
      colorSuccessBg: colors['status.successBg'], colorWarningBg: colors['status.warningBg'],
      colorErrorBg: colors['status.dangerBg'], colorInfoBg: colors['status.infoBg'],
      colorSuccessBorder: colors['border.subtle'], colorWarningBorder: colors['border.subtle'],
      colorErrorBorder: colors['border.subtle'], colorInfoBorder: colors['border.subtle'],
    },
    Table: {
      headerBg: colors['bg.subtle'], headerColor: colors['text.secondary'], rowHoverBg: colors['bg.hover'], cellPaddingBlock: 9, cellPaddingInline: 14, fontSize: 13,
    },
    Tag: {
      defaultColor: colors['text.secondary'], defaultBg: colors['bg.subtle'],
    },
  },
 }
}
export const appTheme = workspaceTheme('light')

