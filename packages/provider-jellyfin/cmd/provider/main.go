// SPDX-FileCopyrightText: 2026 Sofie Finnes <sofie+git@finnes.dev>
// SPDX-License-Identifier: MIT

package main

import (
	"flag"
	"os"
	"time"

	api "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced"
	configapi "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/apis/namespaced/v1beta1"
	localconfig "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/config"
	controllers "github.com/ThePhaseless/terraform-provider-jellyfin/crossplane/internal/controller/namespaced"
	xpcontroller "github.com/crossplane/crossplane-runtime/v2/pkg/controller"
	"github.com/crossplane/crossplane-runtime/v2/pkg/feature"
	"github.com/crossplane/crossplane-runtime/v2/pkg/logging"
	"github.com/crossplane/crossplane-runtime/v2/pkg/ratelimiter"
	tjcontroller "github.com/crossplane/upjet/v2/pkg/controller"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/cache"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
	metricsserver "sigs.k8s.io/controller-runtime/pkg/metrics/server"
)

func main() {
	var interval time.Duration
	flag.DurationVar(&interval, "poll-interval", time.Minute, "Jellyfin drift polling interval")
	flag.Parse()
	namespace := os.Getenv("WATCH_NAMESPACE")
	if namespace == "" {
		panic("WATCH_NAMESPACE is required")
	}
	logger := zap.New(zap.UseDevMode(false))
	ctrl.SetLogger(logger)
	scheme := runtime.NewScheme()
	_ = corev1.AddToScheme(scheme)
	_ = api.AddToScheme(scheme)
	_ = configapi.AddToScheme(scheme)
	mgr, err := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
		Scheme: scheme, Metrics: metricsserver.Options{BindAddress: "0"}, HealthProbeBindAddress: ":8081",
		Cache:  cache.Options{DefaultNamespaces: map[string]cache.Config{namespace: {}}},
		Client: client.Options{Cache: &client.CacheOptions{DisableFor: []client.Object{&corev1.Secret{}}}},
	})
	if err != nil {
		panic(err)
	}
	flags := &feature.Flags{}
	flags.Enable(feature.EnableBetaManagementPolicies)
	options := tjcontroller.Options{Options: xpcontroller.Options{
		Logger: logging.NewLogrLogger(logger), GlobalRateLimiter: ratelimiter.NewGlobal(10),
		PollInterval: interval, MaxConcurrentReconciles: 1, Features: flags,
	}, Provider: localconfig.Namespaced(), SetupFn: setup,
		OperationTrackerStore: tjcontroller.NewOperationStore(logging.NewLogrLogger(logger))}
	if err = controllers.Setup(mgr, options); err != nil {
		panic(err)
	}
	_ = mgr.AddHealthzCheck("healthz", healthz.Ping)
	_ = mgr.AddReadyzCheck("readyz", healthz.Ping)
	if err = mgr.Start(ctrl.SetupSignalHandler()); err != nil {
		panic(err)
	}
}
