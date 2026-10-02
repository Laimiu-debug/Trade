"""Frozen pure context formatters from original store; no original store import.

These formatters only describe a computed signal. Network/sector lookup and
mutable caches are not copied. Source: final-trade store.py static methods.
"""
from trade_app.research.force_rhythm_domain import evaluate_force_rhythm_signal
from trade_app.research.wulong_domain import evaluate_wulong_cluster_signal
from trade_app.research.b1_domain import evaluate_b1_signal
from trade_app.research.emotion_limit_up_domain import evaluate_emotion_limit_up_signal
from trade_app.research.limit_up_arb_domain import evaluate_limit_up_arb_signal
from trade_app.research.trend_king_domain import evaluate_trend_king_signal
TREND_KING_STRATEGY_IDS = {'trend_king_v1','trend_king_limitup_v1','trend_king_rally_v1','trend_king_pullback_v1'}

class SignalContextBuilder:
    @staticmethod
    def _build_ths_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
    ) -> dict[str, object] | None:
        normalized_id = str(strategy_id).strip()
        if normalized_id not in {"ths_main_force_flip_v1", "ths_main_force_golden_cross_v1"}:
            return None
        indicator = snapshot.get("ths_main_retail_signal")
        if not isinstance(indicator, dict):
            return None

        signal_name = "紫转黄"
        primary_signal = "A"
        if normalized_id == "ths_main_force_golden_cross_v1":
            signal_name = "金叉"
            primary_signal = "B"

        trigger_key = "purple_to_yellow" if normalized_id == "ths_main_force_flip_v1" else "golden_cross"
        if not bool(indicator.get(trigger_key, False)):
            return None

        trigger_date = str(indicator.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        main_force = float(indicator.get("main_force", 0.0) or 0.0)
        retail_force = float(indicator.get("retail_force", 0.0) or 0.0)
        main_force_power_score = max(0.0, min(100.0, float(indicator.get("main_force_power_score", 0.0) or 0.0)))
        force_gap_score = max(0.0, min(100.0, float(indicator.get("force_gap_score", 0.0) or 0.0)))
        retail_pressure_score = max(0.0, min(100.0, float(indicator.get("retail_pressure_score", 0.0) or 0.0)))
        explosion_ratio = max(0.0, float(indicator.get("main_force_explosion_ratio", 0.0) or 0.0))
        main_vs_retail_ratio = max(0.0, float(indicator.get("main_vs_retail_ratio", 0.0) or 0.0))
        signal_score = max(0.0, min(100.0, float(indicator.get("signal_score", 0.0) or 0.0)))
        prev_state = str(indicator.get("prev_main_force_state", "flat") or "flat")
        current_state = str(indicator.get("main_force_state", "flat") or "flat")

        if normalized_id == "ths_main_force_flip_v1":
            reason = (
                f"主力由紫转黄 前态={prev_state} 当前={current_state} "
                f"主力={main_force:.2f} 散户={retail_force:.2f} "
                f"爆发分={main_force_power_score:.1f} 对比分={force_gap_score:.1f}"
            )
        else:
            reason = (
                f"主力上穿散户(金叉) 主力={main_force:.2f} 散户={retail_force:.2f} "
                f"爆发分={main_force_power_score:.1f} 对比分={force_gap_score:.1f}"
            )

        event_grade = "C"
        if signal_score >= 80.0:
            event_grade = "A"
        elif signal_score >= 65.0:
            event_grade = "B"

        return {
            "trigger_date": trigger_date,
            "signal_name": signal_name,
            "primary_signal": primary_signal,
            "reason": reason,
            "phase": "主散量能",
            "phase_hint": (
                "按同花顺主力/散户量能公式重算"
                f"；主力爆发比={explosion_ratio:.2f} 主散比={main_vs_retail_ratio:.2f}"
            ),
            "entry_quality_score": signal_score,
            "health_score": main_force_power_score,
            "event_score": force_gap_score,
            "event_strength_score": signal_score,
            "phase_score": main_force_power_score,
            "structure_score": force_gap_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - retail_pressure_score)),
            "event_background_score": main_force_power_score,
            "event_position_score": force_gap_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": force_gap_score,
            "cost_center_shift_score": main_force_power_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_force_rhythm_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "ths_force_rhythm_v1":
            return None
        indicator = snapshot.get("force_rhythm_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_force_rhythm_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        rhythm_score = max(0.0, min(100.0, float(evaluation.get("rhythm_regularity_score", 0.0) or 0.0)))
        main_power_score = max(0.0, min(100.0, float(evaluation.get("main_force_power_score", 0.0) or 0.0)))
        cycle_count = max(0, int(evaluation.get("cycle_count", 0) or 0))
        cycle_cv = float(evaluation.get("cycle_cv", 0.0) or 0.0)
        trough_percentile = max(0.0, min(1.0, float(evaluation.get("trough_percentile", 0.0) or 0.0)))
        main_force = float(indicator.get("main_force", 0.0) or 0.0)
        main_force_state = str(indicator.get("main_force_state", "flat") or "flat")

        pattern_formed = bool(evaluation.get("rhythm_pattern_formed", indicator.get("rhythm_pattern_formed")))
        retail_sell = bool(evaluation.get("retail_sell_signal", indicator.get("retail_sell_signal")))
        retail_pct = max(0.0, min(1.0, float(indicator.get("retail_percentile", 0.0) or 0.0)))
        retail_pct = max(0.0, min(1.0, float(indicator.get("retail_percentile", 0.0) or 0.0)))

        reason = (
            f"主力节奏波谷买点 节奏波={'已形成' if pattern_formed else '未形成'} "
            f"主力={main_force:.2f} 状态={main_force_state} "
            f"周期={cycle_count} 主力分位={trough_percentile:.0%}"
        )
        if retail_sell:
            reason += "（散户卖出压力，已过滤）"
        event_grade = str(evaluation.get("event_grade") or "C")

        return {
            "trigger_date": trigger_date,
            "signal_name": "节奏波谷",
            "primary_signal": "R",
            "reason": reason,
            "phase": "主力节奏",
            "phase_hint": (
                f"周期CV={cycle_cv:.2f} 主力波谷分位={trough_percentile:.0%} "
                f"散户分位={retail_pct:.0%}（主力定节奏，散户仅作卖出参考）"
            ),
            "entry_quality_score": signal_score,
            "health_score": rhythm_score,
            "event_score": main_power_score,
            "event_strength_score": signal_score,
            "phase_score": rhythm_score,
            "structure_score": main_power_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - trough_percentile * 100.0)),
            "event_background_score": rhythm_score,
            "event_position_score": main_power_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": main_power_score,
            "cost_center_shift_score": rhythm_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_wulong_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "wulong_cluster_v1":
            return None
        indicator = snapshot.get("wulong_cluster_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_wulong_cluster_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        health_score = max(0.0, min(100.0, float(evaluation.get("health_score", signal_score) or signal_score)))
        event_score = max(0.0, min(100.0, float(evaluation.get("event_score", signal_score) or signal_score)))
        trend_score = max(0.0, min(100.0, float(evaluation.get("trend_score", signal_score) or signal_score)))
        structure_score = max(0.0, min(100.0, float(evaluation.get("structure_score", signal_score) or signal_score)))
        phase_score = max(0.0, min(100.0, float(evaluation.get("phase_score", signal_score) or signal_score)))
        volatility_score = max(0.0, min(100.0, float(evaluation.get("volatility_score", signal_score) or signal_score)))
        volume_ratio20 = max(0.0, float(evaluation.get("volume_ratio_20", 0.0) or 0.0))
        current_spread_pct = max(0.0, float(evaluation.get("current_spread_pct", 0.0) or 0.0))
        convergence_spread_pct = max(0.0, float(evaluation.get("convergence_spread_pct", 0.0) or 0.0))
        spread_expansion_multiple = max(0.0, float(evaluation.get("spread_expansion_multiple", 0.0) or 0.0))
        convergence_offset_days = max(0, int(evaluation.get("convergence_offset_days", 0) or 0))
        rising_ma_count = max(0, int(evaluation.get("rising_ma_count", 0) or 0))
        ma_values = evaluation.get("ma_values") if isinstance(evaluation.get("ma_values"), dict) else {}
        ma_text = (
            ", ".join(
                f"{key.upper()}={float(value):.2f}"
                for key, value in ma_values.items()
                if str(key).strip() and isinstance(value, (int, float))
            )
            or "MA5/10/20/30/60"
        )
        event_grade = str(evaluation.get("event_grade", "C") or "C").strip().upper()
        if event_grade not in {"A", "B", "C"}:
            event_grade = "C"

        return {
            "trigger_date": trigger_date,
            "signal_name": "五龙聚首",
            "primary_signal": "A",
            "reason": (
                "五龙聚首：5/10/20/30/60日均线由粘合转为多头发散，"
                f"放量倍数={volume_ratio20:.2f}，当前带宽={current_spread_pct * 100:.2f}% ，"
                f"粘合点距今 {convergence_offset_days} 天。"
            ),
            "phase": "均线共振",
            "phase_hint": (
                f"{ma_text}；粘合带宽={convergence_spread_pct * 100:.2f}% ，"
                f"发散倍数={spread_expansion_multiple:.2f}，上拐均线数={rising_ma_count}"
            ),
            "structure_hhh": "MA5|MA10|MA20|MA30|MA60",
            "entry_quality_score": signal_score,
            "health_score": health_score,
            "event_score": event_score,
            "event_strength_score": signal_score,
            "phase_score": phase_score,
            "structure_score": structure_score,
            "trend_score": trend_score,
            "volatility_score": volatility_score,
            "event_background_score": health_score,
            "event_position_score": structure_score,
            "event_vol_price_score": event_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": structure_score,
            "cost_center_shift_score": trend_score,
            "weekly_context_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_b1_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "b1_mtf_v1":
            return None
        indicator = snapshot.get("b1_mtf_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_b1_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(indicator.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        health_score = max(0.0, min(100.0, float(evaluation.get("health_score", signal_score) or signal_score)))
        event_score = max(0.0, min(100.0, float(evaluation.get("event_score", signal_score) or signal_score)))
        trend_score = max(0.0, min(100.0, float(evaluation.get("trend_score", signal_score) or signal_score)))
        structure_score = max(0.0, min(100.0, float(evaluation.get("structure_score", signal_score) or signal_score)))
        phase_score = max(0.0, min(100.0, float(evaluation.get("phase_score", signal_score) or signal_score)))
        volatility_score = max(0.0, min(100.0, float(evaluation.get("volatility_score", signal_score) or signal_score)))
        kdj_j = float(indicator.get("kdj_j", 0))
        volume_ratio = float(indicator.get("volume_ratio", 0))
        monthly_macd = float(indicator.get("monthly_macd", 0))
        weekly_macd = float(indicator.get("weekly_macd", 0))
        event_grade = str(evaluation.get("event_grade", "C") or "C").strip().upper()
        if event_grade not in {"A", "B", "C"}:
            event_grade = "C"

        return {
            "trigger_date": trigger_date,
            "signal_name": "B1多周期",
            "primary_signal": "A",
            "reason": (
                f"B1战法：月MACD多头(柱={monthly_macd:.4f}) + 周DIF>0(柱={weekly_macd:.4f}) + "
                f"日KDJ J={kdj_j:.1f}勾头 + 缩量比={volume_ratio:.4f}"
            ),
            "phase": "多周期共振",
            "phase_hint": (
                f"KDJ_J={kdj_j:.1f} 量比={volume_ratio:.2f} "
                f"月柱={monthly_macd:.4f} 周柱={weekly_macd:.4f}"
            ),
            "entry_quality_score": signal_score,
            "health_score": health_score,
            "event_score": event_score,
            "event_strength_score": signal_score,
            "phase_score": phase_score,
            "structure_score": structure_score,
            "trend_score": trend_score,
            "volatility_score": volatility_score,
            "event_background_score": health_score,
            "event_position_score": structure_score,
            "event_vol_price_score": event_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": structure_score,
            "cost_center_shift_score": trend_score,
            "weekly_context_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_emotion_limit_up_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "emotion_limit_up_v1":
            return None
        indicator = snapshot.get("emotion_limit_up_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_emotion_limit_up_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        day_gain = max(0.0, float(indicator.get("day_gain", 0.0) or 0.0))
        force_ratio = max(0.0, float(indicator.get("force_ratio", 0.0) or 0.0))
        vol_ratio = max(0.0, float(indicator.get("vol_ratio", 0.0) or 0.0))
        signal_age = indicator.get("signal_age_days")
        label = str(evaluation.get("label") or "情绪涨停")
        event_grade = str(evaluation.get("event_grade", "C") or "C").strip().upper()
        if event_grade not in {"A", "B", "C"}:
            event_grade = "C"

        reason = (
            f"情绪涨停：{label} 日涨幅={day_gain:.2f}% "
            f"正/负比={force_ratio:.2f} 量比={vol_ratio:.2f}"
        )
        if signal_age is not None:
            reason += f" 信号距今={int(signal_age)}天"

        return {
            "trigger_date": trigger_date,
            "signal_name": "情绪涨停",
            "primary_signal": "J",
            "reason": reason,
            "phase": "涨停共振",
            "phase_hint": (
                f"天下无双信号+涨停共振；建议次日观察倍量与回撤后再尾盘买入。"
                f" 正/负={force_ratio:.2f} 信号年龄={signal_age if signal_age is not None else '-'}天"
            ),
            "entry_quality_score": signal_score,
            "health_score": signal_score,
            "event_score": signal_score,
            "event_strength_score": signal_score,
            "phase_score": signal_score,
            "structure_score": signal_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - day_gain)),
            "event_background_score": signal_score,
            "event_position_score": signal_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": signal_score,
            "cost_center_shift_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    @staticmethod
    def _build_limit_up_arb_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        if str(strategy_id).strip() != "limit_up_arb_v1":
            return None
        indicator = snapshot.get("limit_up_arb_signal")
        if not isinstance(indicator, dict):
            return None

        evaluation = evaluate_limit_up_arb_signal(indicator, params if isinstance(params, dict) else None)
        if not bool(evaluation.get("signal", False)):
            return None

        trigger_date = str(evaluation.get("trigger_date") or snapshot.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        day_gain = max(0.0, float(indicator.get("day_gain", 0.0) or 0.0))
        volume_ratio_prev = max(0.0, float(indicator.get("volume_ratio_prev", 0.0) or 0.0))
        limit_up_date = str(indicator.get("limit_up_date") or "").strip()
        entry_price = float(indicator.get("entry_price", 0.0) or 0.0)
        event_grade = "A" if signal_score >= 75.0 else ("B" if signal_score >= 50.0 else "C")
        limit_up_text = f"涨停日={limit_up_date} " if limit_up_date else ""
        reason = (
            f"涨停套利：{trigger_date} 收盘已确认（{limit_up_text}"
            f"今涨幅={day_gain:.2f}% 相对昨量={volume_ratio_prev:.2f}x）；"
            f"入场参考价={entry_price:.2f}（触发日收盘价，与回测一致）；"
            f"止盈/止损/持仓天数请在回测页配置。"
        )

        return {
            "trigger_date": trigger_date,
            "signal_name": "涨停套利",
            "primary_signal": "K",
            "reason": reason,
            "phase": "涨停接力",
            "phase_hint": (
                f"收盘后确认信号，非盘中观察池；触发日={trigger_date}，"
                f"入场=触发日收盘价。今涨幅={day_gain:.2f}% 昨量比={volume_ratio_prev:.2f}x；"
                f"板块热度仅作排序参考。"
            ),
            "entry_quality_score": signal_score,
            "health_score": signal_score,
            "event_score": signal_score,
            "event_strength_score": signal_score,
            "phase_score": signal_score,
            "structure_score": signal_score,
            "trend_score": signal_score,
            "volatility_score": max(0.0, min(100.0, 100.0 - day_gain)),
            "event_background_score": signal_score,
            "event_position_score": signal_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": signal_score,
            "cost_center_shift_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }

    def _build_strategy_signal_context(
        self,
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        ths_context = self._build_ths_strategy_signal_context(strategy_id, snapshot)
        if ths_context is not None:
            return ths_context
        rhythm_context = self._build_force_rhythm_strategy_signal_context(strategy_id, snapshot, params)
        if rhythm_context is not None:
            return rhythm_context
        b1_context = self._build_b1_strategy_signal_context(strategy_id, snapshot, params)
        if b1_context is not None:
            return b1_context
        tk_context = self._build_trend_king_strategy_signal_context(strategy_id, snapshot, params)
        if tk_context is not None:
            return tk_context
        elu_context = self._build_emotion_limit_up_strategy_signal_context(strategy_id, snapshot, params)
        if elu_context is not None:
            return elu_context
        lua_context = self._build_limit_up_arb_strategy_signal_context(strategy_id, snapshot, params)
        if lua_context is not None:
            return lua_context
        return self._build_wulong_strategy_signal_context(strategy_id, snapshot, params)

    @staticmethod
    def _resolve_trend_king_mode(strategy_id: str, params: dict[str, object] | None) -> dict[str, object]:
        merged = dict(params) if isinstance(params, dict) else {}
        if "mode" not in merged or not str(merged["mode"]).strip():
            sid = str(strategy_id).strip()
            if "limitup" in sid:
                merged["mode"] = "a"
            elif "rally" in sid:
                merged["mode"] = "b"
            elif "pullback" in sid:
                merged["mode"] = "c"
            else:
                merged["mode"] = "all"
        return merged

    @staticmethod
    def _build_trend_king_strategy_signal_context(
        strategy_id: str,
        snapshot: dict[str, object],
        params: dict[str, object] | None = None,
    ) -> dict[str, object] | None:
        normalized_id = str(strategy_id).strip()
        if normalized_id not in TREND_KING_STRATEGY_IDS:
            return None
        indicator = snapshot.get("trend_king_signal")
        if not isinstance(indicator, dict):
            return None
        merged_params = SignalContextBuilder._resolve_trend_king_mode(strategy_id, params)
        evaluation = evaluate_trend_king_signal(indicator, merged_params)
        if not bool(evaluation.get("signal", False)):
            return None
        trigger_date = str(evaluation.get("trigger_date") or "").strip()
        if not trigger_date:
            return None

        signal_score = max(0.0, min(100.0, float(evaluation.get("signal_score", 0.0) or 0.0)))
        zt_score = max(0.0, min(100.0, float(evaluation.get("zt_score", 0.0) or 0.0)))
        cfm_score = max(0.0, min(100.0, float(evaluation.get("cfm_score", 0.0) or 0.0)))

        mode_label = "趋势为王"
        if normalized_id == "trend_king_limitup_v1" or evaluation.get("mode_a"):
            mode_label = "涨停精选"
        elif normalized_id == "trend_king_rally_v1" or evaluation.get("mode_b"):
            mode_label = "涨势确认"
        elif normalized_id == "trend_king_pullback_v1" or evaluation.get("mode_c"):
            mode_label = "涨停跟踪"

        primary_signal = "A"
        if evaluation.get("mode_b"):
            primary_signal = "B"
        elif evaluation.get("mode_c"):
            primary_signal = "C"

        event_grade = "A" if signal_score >= 80 else ("B" if signal_score >= 65 else "C")
        return {
            "signal_name": mode_label,
            "primary_signal": primary_signal,
            "trigger_date": trigger_date,
            "entry_quality_score": signal_score,
            "health_score": signal_score * 0.7 + zt_score * 0.3,
            "event_score": signal_score * 0.6 + cfm_score * 0.4,
            "event_strength_score": signal_score,
            "phase_score": zt_score,
            "structure_score": cfm_score,
            "trend_score": signal_score,
            "volatility_score": signal_score * 0.5,
            "event_background_score": zt_score,
            "event_position_score": cfm_score,
            "event_vol_price_score": signal_score,
            "event_confirmation_score": signal_score,
            "candle_quality_score": signal_score * 0.8,
            "cost_center_shift_score": signal_score * 0.6,
            "weekly_context_score": signal_score,
            "event_grade": event_grade,
            "event_count": 1,
            "sequence_ok": True,
            "confirmation_status": "confirmed",
        }
