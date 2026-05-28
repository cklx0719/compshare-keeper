#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CompShare 机器每日重启脚本
功能：每天自动对所有 CompShare 机器进行一次开关机操作
"""

import os
import sys
import time
from typing import List, Dict
from ucloud.core import exc
from ucloud.client import Client


class CompShareManager:
    """CompShare 机器管理器"""

    def __init__(self):
        """初始化客户端"""
        public_key = os.getenv("COMPSHARE_PUBLIC_KEY")
        private_key = os.getenv("COMPSHARE_PRIVATE_KEY")

        if not public_key or not private_key:
            raise ValueError("请设置环境变量 COMPSHARE_PUBLIC_KEY 和 COMPSHARE_PRIVATE_KEY")

        self.client = Client({
            "region": "cn-wlcb",
            "public_key": public_key,
            "private_key": private_key,
            "base_url": "https://api.compshare.cn"
        })

        self.zone = "cn-wlcb-01"

    def get_all_instances(self) -> Dict[str, bool]:
        """
        获取所有机器实例及其是否支持无卡启动

        Returns:
            Dict[str, bool]: instance_id -> 是否支持无卡启动
        """
        try:
            print("📋 正在获取所有机器列表...")
            resp = self.client.ucompshare().describe_comp_share_instance({
                "Zone": self.zone,
            })

            instances = resp.get("UHostSet", [])
            result = {}

            for instance in instances:
                instance_id = instance.get("UHostId")
                name = instance.get("Name", "未命名")
                state = instance.get("State", "未知")
                support_without_gpu = instance.get("SupportWithoutGpuStart", False)

                print(f"  - {instance_id}: {name} (状态: {state}, {'支持' if support_without_gpu else '不支持'}无卡启动)")

                if instance_id:
                    result[instance_id] = support_without_gpu
                else:
                    print("⚠️  跳过无 UHostId 的实例")

            cardless_count = sum(1 for v in result.values() if v)
            print(f"✅ 找到 {len(result)} 台机器（{cardless_count} 台支持无卡启动）\n")
            return result

        except exc.UCloudException as e:
            print(f"❌ 获取机器列表失败: {e}")
            raise

    def stop_instances(self, instance_ids: List[str]) -> bool:
        """
        关闭机器实例

        Args:
            instance_ids: 机器 ID 列表

        Returns:
            是否成功
        """
        if not instance_ids:
            print("⚠️  没有需要关闭的机器")
            return True

        try:
            print(f"🛑 正在关闭 {len(instance_ids)} 台机器...")
            for instance_id in instance_ids:
                self.client.ucompshare().stop_comp_share_instance({
                    "Zone": self.zone,
                    "UHostId": instance_id
                })

            print(f"✅ 关机请求已发送\n")
            return True

        except exc.UCloudException as e:
            print(f"❌ 关机失败: {e}")
            return False

    def start_instances(self, instance_ids: List[str], without_gpu: bool = False) -> bool:
        """
        启动机器实例

        Args:
            instance_ids: 机器 ID 列表
            without_gpu: 是否无卡模式开机

        Returns:
            是否成功
        """
        if not instance_ids:
            print("⚠️  没有需要启动的机器")
            return True

        try:
            mode = "无卡模式" if without_gpu else "普通模式"
            print(f"🚀 正在以{mode}启动 {len(instance_ids)} 台机器...")
            for instance_id in instance_ids:
                self.client.ucompshare().start_comp_share_instance({
                    "Zone": self.zone,
                    "UHostId": instance_id,
                    "WithoutGpu": without_gpu
                })

            print(f"✅ 开机请求已发送\n")
            return True

        except exc.UCloudException as e:
            print(f"❌ 开机失败: {e}")
            return False

    def wait_for_status(self, instance_ids: List[str], expected_status: str, timeout: int = 300):
        """
        等待机器达到指定状态

        Args:
            instance_ids: 机器 ID 列表
            expected_status: 期望状态（如 "Stopped", "Running"）
            timeout: 超时时间（秒）
        """
        print(f"⏳ 等待机器状态变为 {expected_status}...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                resp = self.client.ucompshare().describe_comp_share_instance({
                    "Zone": self.zone,
                    "UHostIds": instance_ids
                })

                instances = resp.get("UHostSet", [])
                all_ready = all(
                    inst.get("State") == expected_status
                    for inst in instances
                )

                if all_ready:
                    print(f"✅ 所有机器已达到状态: {expected_status}\n")
                    return True

                time.sleep(10)  # 每 10 秒检查一次

            except exc.UCloudException as e:
                print(f"⚠️  检查状态时出错: {e}")
                time.sleep(10)

        print(f"⚠️  等待超时（{timeout}秒）\n")
        return False


def main():
    """主函数"""
    print("=" * 60)
    print("🔄 CompShare 机器每日重启任务（先开机再关机）")
    print("=" * 60)
    print()

    try:
        manager = CompShareManager()

        # 1. 获取所有机器及其无卡启动支持情况
        instances = manager.get_all_instances()

        if not instances:
            print("ℹ️  没有找到任何机器，任务结束")
            return 0

        # 2. 按是否支持无卡启动分组
        cardless_ids = [iid for iid, supports in instances.items() if supports]
        normal_ids = [iid for iid, supports in instances.items() if not supports]

        # 3. 处理支持无卡启动的机器（无卡模式同一时间只能开机 1 台，按顺序处理）
        for instance_id in cardless_ids:
            if not manager.start_instances([instance_id], without_gpu=True):
                print("❌ 开机失败，终止任务")
                return 1

            manager.wait_for_status([instance_id], "Running", timeout=300)

            print("⏸️  等待 30 秒后关闭机器...\n")
            time.sleep(30)

            if not manager.stop_instances([instance_id]):
                print("❌ 关机失败，终止任务")
                return 1

            manager.wait_for_status([instance_id], "Stopped", timeout=300)

        # 4. 处理不支持无卡启动的普通机器（批量处理）
        if normal_ids:
            if not manager.start_instances(normal_ids, without_gpu=False):
                print("❌ 开机失败，终止任务")
                return 1

            manager.wait_for_status(normal_ids, "Running", timeout=300)

            print("⏸️  等待 30 秒后关闭机器...\n")
            time.sleep(30)

            if not manager.stop_instances(normal_ids):
                print("❌ 关机失败，终止任务")
                return 1

            manager.wait_for_status(normal_ids, "Stopped", timeout=300)

        print("=" * 60)
        print("✅ 重启任务完成！")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
